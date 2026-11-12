# Isolated 300-annotator Supervisely worker-capacity sweep.
# NO PUBLIC SUPERVISELY API CALLS: internal fake Supervisely, separate PostgreSQL.
[CmdletBinding()]
param(
  [int[]]$Workers = @(1, 2, 3),
  [ValidateRange(1, 4)][int]$Validators = 2,
  [ValidateRange(60, 7200)][int]$Seconds = 480,
  [ValidateSet("burst", "staggered")][string]$Scenario = "burst",
  [ValidateRange(0.1, 10.0)][double]$ProfileScale = 1.0,
  [ValidateRange(0, 0.25)][double]$TimeoutRate = 0.0,
  [ValidateRange(0, 0.25)][double]$FailureRate = 0.0,
  [ValidateRange(0, 5.0)][double]$FallbackLatency = 0.05,
  [ValidateRange(100, 20000)][int]$AssumedPublishMs = 1000,
  [string]$Profile = "scripts/benchmark_profiles/measured.json"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
$original = @{}
foreach ($key in @("BENCHMARK_ANNOTATORS", "BENCHMARK_MONITORS",
                    "BENCHMARK_FRAMES", "BENCHMARK_SECONDS_PER_FRAME",
                    "BENCHMARK_BURST", "BENCHMARK_UPLOAD", "BENCHMARK_PROFILE_PATH")) {
  $original[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}
try {
  & docker compose version
  if ($LASTEXITCODE -ne 0) { throw "Docker Compose is unavailable." }
  $env:BENCHMARK_ANNOTATORS = "300"
  $env:BENCHMARK_MONITORS = "12"
  $env:BENCHMARK_FRAMES = "30"
  $env:BENCHMARK_SECONDS_PER_FRAME = "90"
  $env:BENCHMARK_BURST = if ($Scenario -eq "burst") { "1" } else { "0" }
  $env:BENCHMARK_UPLOAD = "0"
  if (Test-Path $Profile) {
    $scriptsPath = (Resolve-Path (Join-Path $root "scripts")).Path
    $resolved = (Resolve-Path $Profile).Path
    if (-not $resolved.StartsWith($scriptsPath)) {
      throw "Put only aggregate timing profiles inside the scripts folder."
    }
    $relative = $resolved.Substring($scriptsPath.Length).TrimStart("\", "/").Replace("\", "/")
    $env:BENCHMARK_PROFILE_PATH = "/bench/$relative"
    Write-Host "Using aggregate latency profile: $Profile"
  }
  else {
    $env:BENCHMARK_PROFILE_PATH = ""
    Write-Host "No measured profile found. Using fallback synthetic latency (not live-calibrated)."
  }
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $rootOut = Join-Path $root (".benchmark-data\sweep-300-$stamp")
  New-Item -ItemType Directory -Force -Path $rootOut | Out-Null
  $results = @()
  foreach ($count in $Workers) {
    if ($count -lt 1 -or $count -gt 4) {
      throw "Worker counts must be between 1 and 4 on the test machine."
    }
    $project = "nightjar-bench-300-$stamp-p$count-v$Validators"
    if (-not $project.StartsWith("nightjar-bench-300-")) {
      throw "Refusing to operate on a non-benchmark Docker project."
    }
    $compose = @("compose", "-p", $project, "-f", "compose.benchmark.yaml")
    $out = Join-Path $rootOut "publishers-$count-validators-$Validators"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $loadName = "$project-load"
    try {
      Write-Host "Starting isolated benchmark: $count publisher(s), $Validators validator(s), $Scenario, $Seconds seconds"
      & docker @compose up -d --build --scale "worker=$count" --scale "validation-worker=$Validators" web worker status-worker validation-worker correction-worker recovery-worker bulk-worker
      if ($LASTEXITCODE -ne 0) { throw "Benchmark environment failed to start." }
      $invariant = [System.Globalization.CultureInfo]::InvariantCulture
      $latencyArg = $FallbackLatency.ToString($invariant)
      $failureArg = $FailureRate.ToString($invariant)
      $scaleArg = $ProfileScale.ToString($invariant)
      $timeoutArg = $TimeoutRate.ToString($invariant)
      & docker @compose run -d --name $loadName load python /bench/benchmark_full.py load --seconds $Seconds --latency $latencyArg --fail-rate $failureArg --profile-scale $scaleArg --timeout-rate $timeoutArg --assumed-publish-ms $AssumedPublishMs
      if ($LASTEXITCODE -ne 0) { throw "Benchmark load process failed to start." }

      Write-Host "Detached load started: $loadName"
      Write-Host "The script samples Docker resources silently during the workload."
      Write-Host "Progress will print every 30 seconds; 480-second tests also run export checks."
      $runStart = Get-Date
      $lastProgress = -30
      $resourceFile = Join-Path $out "resources.jsonl"
      $utf8 = [System.Text.UTF8Encoding]::new($false)
      while ($true) {
        $running = (& docker inspect --format '{{.State.Running}}' $loadName 2>$null)
        if ($running -ne "true") { break }
        $names = @(& docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}') | Where-Object { $_ }
        if ($names.Count -gt 0) {
          $sample = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
          $lines = @(& docker stats --no-stream --format '{{json .}}' @names)
          foreach ($line in $lines) {
            if (-not $line) { continue }
            $data = $line | ConvertFrom-Json
            $data | Add-Member -NotePropertyName sample_ms -NotePropertyValue $sample
            [System.IO.File]::AppendAllText($resourceFile, (($data | ConvertTo-Json -Compress) + "`n"), $utf8)
          }
        }
        $elapsed = [int]((Get-Date) - $runStart).TotalSeconds
        if ($elapsed - $lastProgress -ge 30) {
          Write-Host "Benchmark $count publisher(s), $Validators validator(s): load running, elapsed $elapsed seconds (workload target $Seconds seconds plus setup/export)."
          $lastProgress = $elapsed
        }
        Start-Sleep -Seconds 4
      }
      $exitCode = (& docker inspect --format '{{.State.ExitCode}}' $loadName)
      & docker logs $loadName 2>&1 | Out-File (Join-Path $out "load.log") -Encoding utf8
      & docker cp ($loadName + ":/data/results/.") $out
      if ($LASTEXITCODE -ne 0) { throw "Cannot copy benchmark result files." }
      & docker @compose logs --no-color worker status-worker validation-worker correction-worker recovery-worker bulk-worker api db 2>&1 | Out-File (Join-Path $out "services.log") -Encoding utf8

      if ([int]$exitCode -ne 0) {
        $loadLog = Join-Path $out "load.log"
        Write-Warning "Benchmark load failed (exit $exitCode). Last 80 log lines:"
        if (Test-Path $loadLog) {
          Get-Content -Path $loadLog -Tail 80 | ForEach-Object { Write-Host $_ }
        }
        throw "Benchmark load exited with $exitCode. See $loadLog."
      }
      & python scripts/analyze_full_benchmark.py $out
      if ($LASTEXITCODE -ne 0) { throw "Benchmark result analysis failed." }
      $report = Get-Content (Join-Path $out "summary.json") -Raw | ConvertFrom-Json
      $errors = if ($null -eq $report.http_errors) { 0 } else { @($report.http_errors).Count }
      $published = [int]$report.workload.simulator.published
      $validated = [int]$report.workload.database.review_completed_frames +
                   [int]$report.workload.database.review_ready_frames
      if ($published -eq 0 -or $validated -eq 0) {
        Write-Warning "Invalid throughput measurement: $published published, $validated validated frames."
        Write-Warning "Inspect $out\\services.log and summary.json; validation or publishing is not functioning."
        throw "Benchmark produced no successful publishing/review throughput. Fix the worker/simulator before comparing concurrency."
      }
      $results += [PSCustomObject]@{
        Publishers = $count
        Validators = $Validators
        Scenario = $Scenario
        AssumedPublishMs = $AssumedPublishMs
        Published = $published
        Errors = $errors
        ApiP95ms = [math]::Round([double]$report.workload.p95_ms, 1)
        PeakCpuCores = [math]::Round([double]$report.resources.combined.max_sample_cpu_cores, 2)
        PeakRamMiB = [math]::Round([double]$report.resources.combined.max_sample_working_set_mib, 0)
        Results = $out
      }
      Write-Host "Completed $count-worker benchmark; output: $out"
    }
    finally {
      # Only project-specific test containers and TEST volumes are deleted.
      # NEVER the separately named nightjar-live stack or its real database.
      & docker @compose down --volumes --remove-orphans | Out-Null
    }
  }
  $results | Format-Table -AutoSize
  $results | ConvertTo-Json -Depth 5 | Out-File (Join-Path $rootOut "comparison.json") -Encoding utf8
  Write-Host "Benchmark comparison saved: $rootOut\comparison.json"
  Write-Host "Choose by tail latency, completed operations, errors and RAM headroom; not CPU alone."
}
finally {
  foreach ($entry in $original.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
  }
  Pop-Location
}
