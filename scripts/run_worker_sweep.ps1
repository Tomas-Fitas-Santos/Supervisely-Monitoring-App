# Isolated 300-annotator Supervisely worker-capacity sweep.
# NO PUBLIC SUPERVISELY API CALLS: internal fake Supervisely, separate PostgreSQL.
[CmdletBinding()]
param(
  [ValidateRange(1, 16)][int[]]$Workers = @(1, 2, 3),
  [ValidateSet("publisher", "validation", "correction", "status")][string]$SweepRole = "publisher",
  [ValidateRange(1, 16)][int]$PublisherBaseline = 2,
  [ValidateRange(1, 16)][int]$Validators = 2,
  [ValidateRange(1, 16)][int]$Correctors = 1,
  [ValidateRange(1, 16)][int]$StatusWorkers = 1,
  [ValidateSet("initial", "turnaround")][string]$Phase = "turnaround",
  [ValidateRange(0.0, 1.0)][double]$IncompleteRate = 0.20,
  [ValidateRange(60, 7200)][int]$Seconds = 480,
  [ValidateSet("burst", "staggered")][string]$Scenario = "burst",
  [ValidateRange(0.1, 10.0)][double]$ProfileScale = 1.0,
  [ValidateRange(0, 0.25)][double]$TimeoutRate = 0.0,
  [ValidateRange(0, 0.25)][double]$FailureRate = 0.0,
  [ValidateRange(0, 5.0)][double]$FallbackLatency = 0.05,
  [ValidateRange(100, 20000)][int]$AssumedPublishMs = 1000,
  [string]$Profile = "scripts/benchmark_profiles/measured.json",
  [switch]$Rebuild
)

$ErrorActionPreference = "Stop"

function Get-BenchmarkBuildSignature {
  param([string]$RepoRoot)

  # Only files copied into the API/native/web Docker images affect the cache.
  # Benchmark scripts are bind-mounted into /bench and need no image rebuild.
  $inputs = @(
    ".dockerignore", "compose.benchmark.yaml", "pyproject.toml", "alembic.ini",
    "deploy/Dockerfile.api", "deploy/Dockerfile.web",
    "deploy/docker-entrypoint.sh", "deploy/nginx-live.conf"
  )
  foreach ($directory in @("src", "migrations", "web")) {
    $folder = Join-Path $RepoRoot $directory
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
      throw "Missing Docker build input directory: $directory"
    }
    $inputs += @(Get-ChildItem -LiteralPath $folder -File -Recurse | ForEach-Object {
      $_.FullName.Substring($RepoRoot.Length + 1).Replace("\", "/")
    } | Where-Object {
      $_ -notmatch '(^|/)(node_modules|dist|build|__pycache__|\.pytest_cache|\.ruff_cache|\.next)(/|$)' -and
      $_ -notmatch '\.(pyc|pyo)$'
    })
  }

  $manifest = [System.Text.StringBuilder]::new()
  foreach ($relative in @($inputs | Sort-Object -Unique)) {
    $file = Join-Path $RepoRoot $relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
      throw "Missing Docker build input: $relative"
    }
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    [void]$manifest.AppendLine("$relative $hash")
  }
  $hasher = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($manifest.ToString())
    return [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
  }
  finally { $hasher.Dispose() }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
$original = @{}
foreach ($key in @("BENCHMARK_ANNOTATORS", "BENCHMARK_MONITORS",
                    "BENCHMARK_FRAMES", "BENCHMARK_SECONDS_PER_FRAME",
                    "BENCHMARK_BURST", "BENCHMARK_UPLOAD", "BENCHMARK_PROFILE_PATH",
                    "BENCHMARK_PHASE", "BENCHMARK_INCOMPLETE_RATE")) {
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
  $env:BENCHMARK_PHASE = $Phase
  $env:BENCHMARK_INCOMPLETE_RATE = $IncompleteRate.ToString([System.Globalization.CultureInfo]::InvariantCulture)
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
  # Shared benchmark images survive test teardown. Rebuild once if the
  # Docker build inputs changed, a required image is missing, or -Rebuild is set.
  # Never build inside the publisher/validator comparison loop.
  $cacheDir = Join-Path $root ".benchmark-data"
  New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
  $buildStamp = Join-Path $cacheDir "worker-sweep-build.sha256"
  $fingerprint = Get-BenchmarkBuildSignature -RepoRoot $root
  $cachedFingerprint = if (Test-Path -LiteralPath $buildStamp) {
    (Get-Content -LiteralPath $buildStamp -Raw).Trim()
  } else { "" }
  $missingImages = @()
  foreach ($image in @("nightjar-benchmark-api:latest",
                        "nightjar-benchmark-runtime:latest",
                        "nightjar-benchmark-web:latest")) {
    & docker image inspect --format '{{.Id}}' $image 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $missingImages += $image }
  }
  if ($Rebuild -or $fingerprint -ne $cachedFingerprint -or $missingImages.Count -gt 0) {
    if ($Rebuild) {
      Write-Host "Rebuilding benchmark images because -Rebuild was requested."
    }
    elseif ($missingImages.Count -gt 0) {
      Write-Host "Building missing benchmark images: $($missingImages -join ', ')"
    }
    else {
      Write-Host "Benchmark Docker build inputs changed; building images once."
    }
    & docker compose -p nightjar-bench-build -f compose.benchmark.yaml build api worker web
    if ($LASTEXITCODE -ne 0) { throw "Benchmark image build failed; cached signature not updated." }
    [System.IO.File]::WriteAllText(
      $buildStamp, $fingerprint + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Benchmark images are ready and will be reused across tests."
  }
  else {
    Write-Host "Reusing unchanged benchmark images (no Docker build)."
  }

  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $rootOut = Join-Path $root (".benchmark-data\sweep-300-$stamp")
  New-Item -ItemType Directory -Force -Path $rootOut | Out-Null
  $results = @()
  if (@($Workers | Select-Object -Unique).Count -ne $Workers.Count) {
    throw "Specify distinct worker counts so test projects and results are unique."
  }
  foreach ($count in $Workers) {
    # -Workers selects the varying count for the chosen role; all other
    # roles retain their explicitly configured baseline replicas.
    $publishers = if ($SweepRole -eq "publisher") { $count } else { $PublisherBaseline }
    $validatorsRun = if ($SweepRole -eq "validation") { $count } else { $Validators }
    $correctorsRun = if ($SweepRole -eq "correction") { $count } else { $Correctors }
    $statusRun = if ($SweepRole -eq "status") { $count } else { $StatusWorkers }
    # Each benchmark container has a finite CPU/memory limit, but the host
    # may be overcommitted at higher replica counts. Warn without falsifying
    # the result: Docker stats and actual running replicas remain authoritative.
    $allocatedCpus = 0.75 * ($publishers + $validatorsRun + $statusRun) +
                     0.5 * $correctorsRun + 3.5
    $allocatedMiB = 768 * ($publishers + $validatorsRun + $statusRun) +
                    512 * $correctorsRun + 4096
    $dockerCpus = & docker info --format '{{.NCPU}}' 2>$null
    $dockerMemory = & docker info --format '{{.MemTotal}}' 2>$null
    if ($LASTEXITCODE -ne 0) {
      throw "Cannot inspect Docker resource capacity before the benchmark."
    }
    $dockerMemoryMiB = [double]$dockerMemory / 1MB
    if ($allocatedCpus -gt [double]$dockerCpus) {
      Write-Warning ("Requested containers have {0:N1} combined CPU limits but Docker exposes {1} CPUs. High worker counts may slow each other down." -f $allocatedCpus, $dockerCpus)
    }
    if ($allocatedMiB -gt 0.8 * $dockerMemoryMiB) {
      Write-Warning ("Combined container memory limits are ~{0:N0} MiB, versus {1:N0} MiB available to Docker. Watch Docker memory pressure; those are limits, not actual usage." -f $allocatedMiB, $dockerMemoryMiB)
    }
    $project = "nightjar-bench-300-$stamp-$Phase-p$publishers-v$validatorsRun-c$correctorsRun-s$statusRun"
    if (-not $project.StartsWith("nightjar-bench-300-")) {
      throw "Refusing to operate on a non-benchmark Docker project."
    }
    $compose = @("compose", "-p", $project, "-f", "compose.benchmark.yaml")
    $out = Join-Path $rootOut "$Phase-publishers-$publishers-validators-$validatorsRun-correctors-$correctorsRun-status-$statusRun"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $loadName = "$project-load"
    try {
      Write-Host "Starting isolated benchmark: $Phase, $publishers publisher(s), $validatorsRun validator(s), $correctorsRun corrector(s), $statusRun status worker(s), $Scenario, $Seconds seconds"
      & docker @compose up -d --no-build --scale "worker=$publishers" --scale "validation-worker=$validatorsRun" --scale "correction-worker=$correctorsRun" --scale "status-worker=$statusRun" web worker status-worker validation-worker correction-worker recovery-worker bulk-worker
      if ($LASTEXITCODE -ne 0) { throw "Benchmark environment failed to start." }
      # A capacity comparison is invalid if Compose silently starts fewer
      # replicas than requested. Confirm the actual live role counts.
      foreach ($role in @(
        @{ Name = "worker"; Expected = $publishers },
        @{ Name = "validation-worker"; Expected = $validatorsRun },
        @{ Name = "correction-worker"; Expected = $correctorsRun },
        @{ Name = "status-worker"; Expected = $statusRun }
      )) {
        $actual = @(& docker ps --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$($role.Name)" --format '{{.ID}}') | Where-Object { $_ }
        if ($actual.Count -ne $role.Expected) {
          throw "Expected $($role.Expected) running $($role.Name) containers, found $($actual.Count). Capacity data would be invalid."
        }
      }

      $invariant = [System.Globalization.CultureInfo]::InvariantCulture
      $latencyArg = $FallbackLatency.ToString($invariant)
      $failureArg = $FailureRate.ToString($invariant)
      $scaleArg = $ProfileScale.ToString($invariant)
      $timeoutArg = $TimeoutRate.ToString($invariant)
      & docker @compose run -d --no-deps --name $loadName load python /bench/benchmark_full.py load --seconds $Seconds --latency $latencyArg --fail-rate $failureArg --profile-scale $scaleArg --timeout-rate $timeoutArg --assumed-publish-ms $AssumedPublishMs
      if ($LASTEXITCODE -ne 0) { throw "Benchmark load process failed to start." }
      # 'docker compose run' must NOT re-evaluate load's depends_on graph:
      # otherwise Compose silently scales publisher/validator replicas back to 1.
      # Recheck after the one-off load is started; the pre-launch check alone
      # did not detect this regression in the 2026-11-12 benchmark.
      foreach ($role in @(
        @{ Name = "worker"; Expected = $publishers },
        @{ Name = "validation-worker"; Expected = $validatorsRun },
        @{ Name = "correction-worker"; Expected = $correctorsRun },
        @{ Name = "status-worker"; Expected = $statusRun }
      )) {
        $actual = @(& docker ps --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$($role.Name)" --format '{{.ID}}') | Where-Object { $_ }
        if ($actual.Count -ne $role.Expected) {
          throw "Benchmark load altered worker scaling: expected $($role.Expected) running $($role.Name), found $($actual.Count). No valid capacity measurement."
        }
      }

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
          # A crashed/recreated replica would corrupt the scaling comparison.
          # Fail immediately rather than silently reporting less capacity.
          foreach ($role in @(
            @{ Name = "worker"; Expected = $publishers },
            @{ Name = "validation-worker"; Expected = $validatorsRun },
            @{ Name = "correction-worker"; Expected = $correctorsRun },
            @{ Name = "status-worker"; Expected = $statusRun }
          )) {
            $active = @(& docker ps --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$($role.Name)" --format '{{.ID}}') | Where-Object { $_ }
            if ($active.Count -ne $role.Expected) {
              throw "Benchmark worker count changed during load: expected $($role.Expected) $($role.Name), found $($active.Count). See service logs; results invalid."
            }
          }
          Write-Host "Benchmark $SweepRole=$count ($publishers publisher(s), $validatorsRun validator(s), $correctorsRun corrector(s), $statusRun status worker(s)): load running, elapsed $elapsed seconds (workload target $Seconds seconds plus setup/export)."
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
      $confirmed = [int]$report.publication_cohort.confirmed_count
      $firstJob = $report.publication_cohort.first_confirmed_ms
      $job150 = $report.publication_cohort.one_hundred_fiftieth_confirmed_ms
      $job290 = $report.publication_cohort.two_hundred_ninetieth_confirmed_ms
      $job300 = $report.publication_cohort.three_hundredth_confirmed_ms
      $states = $report.workload.database.operation_kinds
      $attentionCount = [int]$report.workload.database.operation_states.attention
      $operationErrors = @($report.workload.database.operation_errors)
      $integrityFailure = ($attentionCount -gt 0 -or $operationErrors.Count -gt 0)
      if ($integrityFailure) {
        Write-Warning "$attentionCount operations need attention; $($operationErrors.Count) error categories. This configuration is NOT a successful capacity result."
        foreach ($problem in $operationErrors) {
          Write-Warning "Operation failure: $problem"
        }
        Write-Warning "Inspect $out\services.log for synthetic tracebacks. No production systems were accessed."
      }
      $corrections = [int]$states.'restart_job:done'
      $validations = [int]$states.'validate_submission:done'
      if (($Phase -eq "initial" -and $confirmed -eq 0) -or
          ($Phase -eq "turnaround" -and ($validations -eq 0 -or $validated -eq 0))) {
        Write-Warning "No meaningful $Phase throughput: $confirmed published, $validations validations, $validated ready frames."
        Write-Warning "Inspect $out\\services.log and summary.json."
        throw "Benchmark stage did not progress. Fix the fixture or workers before comparing roles."
      }
      if ($Phase -eq "initial" -and $confirmed -lt 300) {
        Write-Warning "Only $confirmed/300 original jobs confirmed within $Seconds seconds. Full 300-job release time is not yet measured; extend -Seconds."
      }
      $results += [PSCustomObject]@{
        SweepRole = $SweepRole
        SweepCount = $count
        Valid = -not $integrityFailure
        AttentionOperations = $attentionCount
        OperationErrorKinds = $operationErrors.Count
        Publishers = $publishers
        Phase = $Phase
        Validators = $validatorsRun
        Correctors = $correctorsRun
        StatusWorkers = $statusRun
        IncompleteRate = $IncompleteRate
        Scenario = $Scenario
        AssumedPublishMs = $AssumedPublishMs
        Published = $published
        ConfirmedInitialJobs = if ($Phase -eq "initial") { $confirmed } else { $null }
        InitialFirstMs = if ($Phase -eq "initial") { $firstJob } else { $null }
        Initial150thMs = if ($Phase -eq "initial") { $job150 } else { $null }
        Initial290thMs = if ($Phase -eq "initial") { $job290 } else { $null }
        Initial300thMs = if ($Phase -eq "initial") { $job300 } else { $null }
        ValidatedTasks = $validations
        ReviewReadyFrames = $validated
        CorrectionsPublished = $corrections
        NextJobsConfirmed = $report.worker_timing.next_jobs_confirmed
        SubmissionDetectionP95ms = $report.submission_detection.p95_ms
        ValidationQueueP95ms = $report.worker_timing.stage_lags.validation_queue_wait.p95_ms
        ValidationP95ms = $report.worker_timing.stage_lags.validation_duration.p95_ms
        NextJobWaitP95ms = $report.worker_timing.stage_lags.complete_validation_to_new_job.p95_ms
        CorrectionWaitP95ms = $report.worker_timing.stage_lags.correction_queued_to_confirmed.p95_ms
        Errors = $errors
        ApiP95ms = [math]::Round([double]$report.workload.p95_ms, 1)
        PeakCpuCores = [math]::Round([double]$report.resources.combined.max_sample_cpu_cores, 2)
        PeakRamMiB = [math]::Round([double]$report.resources.combined.max_sample_working_set_mib, 0)
        Results = $out
      }
      Write-Host "Completed $SweepRole=$count benchmark; output: $out"
    }
    finally {
      # Only project-specific test containers and TEST volumes are deleted.
      # NEVER the separately named nightjar-live stack or its real database.
      & docker @compose down --volumes --remove-orphans | Out-Null
    }
  }
  $results | Sort-Object SweepCount | Format-Table -AutoSize
  $results | ConvertTo-Json -Depth 5 | Out-File (Join-Path $rootOut "comparison.json") -Encoding utf8
  Write-Host "Benchmark comparison saved: $rootOut\comparison.json"
  if (@($results | Where-Object { -not $_.Valid }).Count -gt 0) {
    Write-Warning "One or more configurations are INVALID (native operations in attention/error states). Do not size production workers from these rows."
  }
  Write-Host "Choose per-role worker counts by complete job turnaround (p95/p99), 300-job drain time, backlog and errors; not CPU alone."
}
finally {
  foreach ($entry in $original.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
  }
  Pop-Location
}
