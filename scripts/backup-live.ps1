# Online PostgreSQL/configuration snapshots. Does not stop workers or alter event data.
[CmdletBinding()]
param(
  [string]$Destination = "",
  [string]$EnvironmentFile = "",
  [string]$DatabaseContainer = "nightjar-live-db-1",
  [string]$StatusFile = "",
  [switch]$Continuous,
  [ValidateRange(60, 86400)][int]$IntervalSeconds = 300
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $EnvironmentFile) { $EnvironmentFile = Join-Path $projectRoot ".env.live" }
if (-not $Destination) { $Destination = Join-Path $projectRoot "backups\recovery" }
$EnvironmentFile = (Resolve-Path -LiteralPath $EnvironmentFile).Path
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$snapshotRoot = (Resolve-Path -LiteralPath $Destination).Path
if (-not $StatusFile) {
  $mediaMatch = [regex]::Match((Get-Content -LiteralPath $EnvironmentFile -Raw), '(?m)^NIGHTJAR_MEDIA_DIR=([^\r\n]+)')
  $mediaLocation = if ($mediaMatch.Success) { $mediaMatch.Groups[1].Value.Trim().Trim('"').Trim("'") } else { ".nightjar-live-data" }
  if (-not [IO.Path]::IsPathRooted($mediaLocation)) { $mediaLocation = Join-Path $projectRoot $mediaLocation }
  $StatusFile = Join-Path $mediaLocation "operations-health/backup.json"
}

function Write-BackupReceipt([bool]$Failed, [string]$CompletedUtc = "") {
  try {
    if (-not $CompletedUtc -and (Test-Path -LiteralPath $StatusFile)) {
      $CompletedUtc = (Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json).completed_utc
    }
    $statusParent = Split-Path -Parent ([IO.Path]::GetFullPath($StatusFile))
    New-Item -ItemType Directory -Path $statusParent -Force | Out-Null
    $receiptTemp = Join-Path $statusParent ("backup-" + [guid]::NewGuid().ToString("N") + ".tmp")
    @{ completed_utc = $CompletedUtc; last_attempt_failed = $Failed; restore_tested = $false } |
      ConvertTo-Json | Set-Content -LiteralPath $receiptTemp -Encoding utf8
    Move-Item -LiteralPath $receiptTemp -Destination $StatusFile -Force
  } catch { Write-Warning "Could not publish the health-page backup receipt. Check media-volume access." }
}

function Check-BackupCommand([string]$Action) {
  if ($LASTEXITCODE -ne 0) { throw "$Action failed; existing snapshots were retained." }
}

function Save-DatabaseSnapshot {
  $started = [DateTime]::UtcNow
  $settingsText = Get-Content -LiteralPath $EnvironmentFile -Raw
  $userMatch = [regex]::Match($settingsText, '(?m)^POSTGRES_USER=([^\r\n]+)')
  $nameMatch = [regex]::Match($settingsText, '(?m)^POSTGRES_DB=([^\r\n]+)')
  if (-not $userMatch.Success -or -not $nameMatch.Success) {
    throw "The environment file must contain POSTGRES_USER and POSTGRES_DB."
  }
  $databaseUser = $userMatch.Groups[1].Value.Trim()
  $databaseName = $nameMatch.Groups[1].Value.Trim()
  $snapshotId = $started.ToString("yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
  $partial = Join-Path $snapshotRoot (".partial-" + $snapshotId)
  $finished = Join-Path $snapshotRoot $snapshotId
  $remoteDump = "/tmp/nightjar-recovery-$snapshotId.dump"
  New-Item -ItemType Directory -Path $partial | Out-Null
  try {
    # pg_dump takes a consistent online transaction snapshot, including plans,
    # assignments, leases, review decisions, scores and immutable frame snapshots.
    & docker exec $DatabaseContainer pg_dump -U $databaseUser -d $databaseName -Fc -f $remoteDump
    Check-BackupCommand "PostgreSQL snapshot"
    & docker exec $DatabaseContainer pg_restore --list $remoteDump | Out-Null
    Check-BackupCommand "Archive structure verification"
    $dumpPath = Join-Path $partial "database.dump"
    & docker cp "${DatabaseContainer}:$remoteDump" $dumpPath
    Check-BackupCommand "Snapshot copy"
    if ((Get-Item -LiteralPath $dumpPath).Length -le 0) { throw "Empty database snapshot." }
    # Preserve the original encryption key; never generate a replacement on restore.
    Copy-Item -LiteralPath $EnvironmentFile -Destination (Join-Path $partial ".env.live")
    $manifest = [ordered]@{
      schema = "nightjar-database-recovery-v1"
      started_utc = $started.ToString("o")
      completed_utc = [DateTime]::UtcNow.ToString("o")
      archive_structure_verified = $true
      restore_tested = $false
      files = @("database.dump", ".env.live") | ForEach-Object {
        $itemPath = Join-Path $partial $_
        @{ name = $_; bytes = (Get-Item -LiteralPath $itemPath).Length;
           sha256 = (Get-FileHash -LiteralPath $itemPath -Algorithm SHA256).Hash }
      }
      not_included = @("Uploaded and mounted media files", "External Supervisely state")
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $partial "manifest.json") -Encoding utf8
    # Both resolved absolute paths must remain direct children of the chosen
    # backup destination before moving this directory into its published name.
    if ((Split-Path -Parent (Resolve-Path -LiteralPath $partial).Path) -ne $snapshotRoot -or
        (Split-Path -Parent ([IO.Path]::GetFullPath($finished))) -ne $snapshotRoot) {
      throw "Snapshot destination escaped the backup directory."
    }
    Move-Item -LiteralPath $partial -Destination $finished
    Write-BackupReceipt -Failed $false -CompletedUtc $manifest.completed_utc
    Write-Host "Database/configuration snapshot verified: $finished"
  }
  finally {
    # Only this invocation's exact temporary file inside the container is removed.
    & docker exec $DatabaseContainer rm -f $remoteDump | Out-Null
  }
}

do {
  try { Save-DatabaseSnapshot }
  catch {
    Write-BackupReceipt -Failed $true
    if (-not $Continuous) { throw }
    Write-Warning "Recovery snapshot failed. Previous snapshots are retained; retrying next interval."
  }
  if ($Continuous) { Start-Sleep -Seconds $IntervalSeconds }
} while ($Continuous)
