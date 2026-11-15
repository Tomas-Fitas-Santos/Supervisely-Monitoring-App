# Upgrade the persistent Nightjar live stack without deleting any database volumes
# or Supervisely records. Run from any folder after pulling main.
[CmdletBinding()]
param(
  [switch]$StartWorker,
  [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
try {
  if (-not (Test-Path ".env.live")) {
    throw ("Missing .env.live. For a NEW installation run scripts/init-live-test.ps1. " +
           "For an existing installation RESTORE the original .env.live; " +
           "never generate replacement database or encryption secrets.")
  }

  function Check-LastExit([string]$operation) {
    if ($LASTEXITCODE -ne 0) {
      throw "$operation failed with exit code $LASTEXITCODE. Volumes were NOT deleted."
    }
  }

  $environment = Get-Content ".env.live" -Raw
  $userMatch = [regex]::Match($environment, '(?m)^POSTGRES_USER=([^\r\n]+)')
  $dbMatch = [regex]::Match($environment, '(?m)^POSTGRES_DB=([^\r\n]+)')
  if (-not $userMatch.Success -or -not $dbMatch.Success) {
    throw "POSTGRES_USER and POSTGRES_DB must exist in .env.live."
  }
  $dbUser = $userMatch.Groups[1].Value.Trim()
  $dbName = $dbMatch.Groups[1].Value.Trim()
  $compose = @("--env-file", ".env.live", "-f", "compose.live.yaml")

  & docker compose version
  Check-LastExit "Docker Compose preflight"
  & docker compose @compose config --quiet
  Check-LastExit "Compose configuration validation"

  # Fixed production fleet. Preserve whether ANY SDK lane was previously
  # active; never restart only the old all-in-one worker after deployment.
  $nativeCounts = [ordered]@{
    "worker" = 4
    "validation-worker" = 3
    "correction-worker" = 3
    "status-worker" = 1
    "recovery-worker" = 1
    "bulk-worker" = 1
    "mirror-worker" = 1
  }
  $nativeServices = @($nativeCounts.Keys)
  $nativeWasRunning = $false
  foreach ($service in $nativeServices) {
    $runningIds = @(& docker compose @compose --profile native ps --status running -q $service)
    Check-LastExit "Checking $service state"
    if (@($runningIds | Where-Object { $_ }).Count -gt 0) {
      $nativeWasRunning = $true
    }
  }

  # Build before pausing the event. Preparation never starts/stops containers,
  # runs migrations, changes the tunnel or writes to the event database.
  $buildServices = @("api", "web", "indexer", "migrate")
  if ($StartWorker -or $nativeWasRunning) { $buildServices += $nativeServices }
  $releaseId = Get-Date -Format "yyyyMMdd-HHmmss"
  $previousImages = @()
  foreach ($service in $buildServices) {
    $containerId = (& docker compose @compose --profile native ps -q $service | Select-Object -First 1)
    Check-LastExit "Finding previous $service image"
    if ($containerId) {
      $imageId = (& docker inspect --format '{{.Image}}' $containerId)
      Check-LastExit "Recording previous $service image"
      $rollbackTag = "nightjar-rollback/${service}:$releaseId"
      & docker image inspect --format '{{.Id}}' $imageId 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) {
        & docker image tag $imageId $rollbackTag
        Check-LastExit "Retaining rollback image for $service"
      }
      else {
        # Docker can retain a running container after its image was removed.
        # Preserve its filesystem before replacement; mounted data is excluded.
        # Keep this snapshot local: its configuration includes runtime env vars.
        Write-Host "Original $service image unavailable; retaining a local container snapshot."
        & docker container commit --pause=false $containerId $rollbackTag | Out-Null
        Check-LastExit "Snapshotting previous $service container"
        $imageId = (& docker image inspect --format '{{.Id}}' $rollbackTag)
        Check-LastExit "Recording rollback snapshot for $service"
      }
      $previousImages += @{ service = $service; image = $imageId; rollback_tag = $rollbackTag }
    }
  }
  $releaseFolder = Join-Path $root "backups\deployments"
  New-Item -ItemType Directory -Path $releaseFolder -Force | Out-Null
  @{ release = $releaseId; previous_images = $previousImages; deployed = $false } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $releaseFolder "$releaseId.json") -Encoding utf8
  & docker compose @compose --profile native build @buildServices
  Check-LastExit "Preparing application and worker images"
  if ($PrepareOnly) {
    Write-Host "Release images prepared. Live containers, database, datasets and tunnel are unchanged."
    Write-Host "Rollback image inventory: backups/deployments/$releaseId.json"
    return
  }

  # Pause every SDK lane before the consistent PostgreSQL backup and upgrade.
  & docker compose @compose --profile native stop @nativeServices api web indexer
  Check-LastExit "Stopping web/API/native worker fleet"
  & docker compose @compose up -d --wait db
  Check-LastExit "Starting existing PostgreSQL service"

  $dbContainer = (& docker compose @compose ps -q db | Select-Object -First 1)
  Check-LastExit "Finding persistent database"
  if (-not $dbContainer) { throw "The PostgreSQL container is missing." }

  $backups = Join-Path $root "backups"
  New-Item -ItemType Directory -Path $backups -Force | Out-Null
  $backupFile = Join-Path $backups ("nightjar-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".dump")
  $temporary = "/tmp/nightjar-before-upgrade.dump"
  try {
    & docker exec $dbContainer pg_dump -U $dbUser -d $dbName -Fc -f $temporary
    Check-LastExit "Backing up PostgreSQL"
    & docker cp "${dbContainer}:$temporary" $backupFile
    Check-LastExit "Copying the PostgreSQL backup"
  }
  finally {
    & docker exec $dbContainer rm -f $temporary | Out-Null
  }
  if (-not (Test-Path $backupFile) -or (Get-Item $backupFile).Length -le 0) {
    throw "PostgreSQL backup was not written successfully. Deployment aborted."
  }
  Write-Host "Persistent database backed up to: $backupFile"
  Write-Host "Also keep a separate backup of NIGHTJAR_MEDIA_DIR and the original .env.live."

  # Compose migrate service applies Alembic HEAD before API startup.
  & docker compose @compose up -d --no-build --force-recreate migrate api web indexer
  Check-LastExit "Building and starting Nightjar services"

  & docker compose @compose ps
  Check-LastExit "Checking containers"
  $portMatch = [regex]::Match($environment, '(?m)^NIGHTJAR_LIVE_TEST_PORT=([0-9]+)')
  $healthPort = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { "8080" }
  & curl.exe --fail --silent --show-error "http://127.0.0.1:$healthPort/health/ready"
  Check-LastExit "Nightjar health/ready"
  Write-Host ""
  Write-Host "Nightjar web/API is healthy on port $healthPort. Mounted dataset indexer is enabled."

  # Start every role from the same rebuilt code. --no-deps prevents
  # re-running the migration or recreating the persistent PostgreSQL service.
  if ($StartWorker -or $nativeWasRunning) {
    $scales = @("--scale", "worker=4",
                "--scale", "validation-worker=3",
                "--scale", "correction-worker=3")
    & docker compose @compose --profile native up -d --no-build --no-deps @scales @nativeServices
    Check-LastExit "Starting dedicated production SDK worker fleet"
    foreach ($service in $nativeServices) {
      $runningIds = @(& docker compose @compose --profile native ps --status running -q $service)
      Check-LastExit "Checking $service"
      $actual = @($runningIds | Where-Object { $_ }).Count
      $expected = $nativeCounts[$service]
      if ($actual -ne $expected) {
        throw "Expected $expected running $service container(s); found $actual. Database and jobs were not deleted."
      }
      Write-Host "$service : $actual running"
    }
    Write-Host "Production native topology active: 4 publishers, 3 validators, 3 correctors, 1 status, 1 recovery, 1 bulk, 1 mirror."
  }
  else {
    Write-Host "SDK workers remain stopped. Start after verifying shared Supervisely Team/Workspace credentials:"
    Write-Host ".\\scripts\\deploy-live.ps1 -StartWorker"
  }
  @{ release = $releaseId; previous_images = $previousImages; deployed = $true;
     database_backup = $backupFile; completed_utc = [DateTime]::UtcNow.ToString("o") } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $releaseFolder "$releaseId.json") -Encoding utf8
}
finally {
  Pop-Location
}
