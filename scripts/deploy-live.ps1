# Upgrade the persistent Nightjar live stack without deleting any database volumes
# or Supervisely records. Run from any folder after pulling main.
[CmdletBinding()]
param(
  [switch]$StartWorker
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

  # Remember whether the native worker was active before deployment. Previously
  # this script ALWAYS stopped it and silently left it stopped unless the
  # organiser passed -StartWorker, stranding queued dataset mirrors.
  $workerWasRunning = $false
  $runningWorkerIds = @(& docker compose @compose --profile native ps --status running -q worker)
  Check-LastExit "Checking current Supervisely worker state"
  $workerWasRunning = @($runningWorkerIds | Where-Object { $_ }).Count -gt 0
  $mirrorIds = @(& docker compose @compose --profile native ps --status running -q mirror-worker)
  Check-LastExit "Checking current Supervisely mirror state"
  $mirrorWasRunning = @($mirrorIds | Where-Object { $_ }).Count -gt 0

  # Pause native writes before the consistent PostgreSQL backup and upgrade.
  & docker compose @compose --profile native stop worker mirror-worker api web indexer
  Check-LastExit "Stopping web/API/native worker"
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
  & docker compose @compose up -d --build --force-recreate migrate api web indexer
  Check-LastExit "Building and starting Nightjar services"

  & docker compose @compose ps
  Check-LastExit "Checking containers"
  $portMatch = [regex]::Match($environment, '(?m)^NIGHTJAR_LIVE_TEST_PORT=([0-9]+)')
  $healthPort = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { "8080" }
  & curl.exe --fail --silent --show-error "http://127.0.0.1:$healthPort/health/ready"
  Check-LastExit "Nightjar health/ready"
  Write-Host ""
  Write-Host "Nightjar web/API is healthy on port $healthPort. Mounted dataset indexer is enabled."

  # The SDK upload lane has its own container so large media transfers
  # do not occupy the publisher/status/validation worker. Preserve running
  # state on upgrade, and start both with -StartWorker after verification.
  if ($StartWorker -or $workerWasRunning) {
    & docker compose @compose --profile native up -d --build worker
    Check-LastExit "Starting native job worker"
    $jobIds = @(& docker compose @compose --profile native ps --status running -q worker)
    Check-LastExit "Checking native job worker"
    if (@($jobIds | Where-Object { $_ }).Count -eq 0) {
      throw "Supervisely job worker is not running; inspect its logs."
    }
  }
  if ($StartWorker -or $workerWasRunning -or $mirrorWasRunning) {
    & docker compose @compose --profile native up -d --build mirror-worker
    Check-LastExit "Starting dedicated source upload worker"
    $uploadIds = @(& docker compose @compose --profile native ps --status running -q mirror-worker)
    Check-LastExit "Checking dedicated source upload worker"
    if (@($uploadIds | Where-Object { $_ }).Count -eq 0) {
      throw "Supervisely upload worker is not running; inspect mirror-worker logs."
    }
    Write-Host "Media uploads and native job processing now run in separate containers."
  }
  if (-not ($StartWorker -or $workerWasRunning -or $mirrorWasRunning)) {
    Write-Host "SDK workers were stopped and remain stopped; verified integration is required before starting them."
    Write-Host "docker compose --env-file .env.live -f compose.live.yaml --profile native up -d worker mirror-worker"
  }
}
finally {
  Pop-Location
}
