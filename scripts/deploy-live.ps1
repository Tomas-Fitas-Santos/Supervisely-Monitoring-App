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
    & (Join-Path $PSScriptRoot "init-live-test.ps1")
  }
  if (-not (Test-Path ".env.live")) {
    throw "Missing .env.live. Do not deploy without preserved configuration."
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

  # Pause native writes before the consistent PostgreSQL backup and upgrade.
  & docker compose @compose --profile native stop worker api web
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
  & docker compose @compose up -d --build --force-recreate migrate api web
  Check-LastExit "Building and starting Nightjar services"

  & docker compose @compose ps
  Check-LastExit "Checking containers"
  & curl.exe --fail --silent --show-error http://127.0.0.1:8080/health/ready
  Check-LastExit "Nightjar health/ready"
  Write-Host ""
  Write-Host "Nightjar web/API is healthy on port 8080."

  if ($StartWorker) {
    # Use only after the organiser has verified the native team/workspace
    # integration token and intended Supervisely target in Settings.
    & docker compose @compose --profile native up -d --build worker
    Check-LastExit "Starting Supervisely worker"
    Write-Host "Native worker started."
  }
  else {
    Write-Host "Native worker remains stopped. Re-run with -StartWorker after verifying live Supervisely Settings."
  }
}
finally {
  Pop-Location
}
