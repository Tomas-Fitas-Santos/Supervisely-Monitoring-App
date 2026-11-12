# Run on the Windows PC that hosts Docker Desktop; does not upload/copy media.
# Interactive: .\scripts\link-local-dataset.ps1 -StartWorker
# Explicit:    .\scripts\link-local-dataset.ps1 -DatasetFolder "D:\BirdDatasets\BirdGaze2026" -StartWorker
[CmdletBinding()]
param(
  [string]$DatasetFolder,
  [switch]$StartWorker,
  [switch]$ReplaceMount
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $root ".env.live"
if ($env:OS -ne "Windows_NT") { throw "Run this command on the Windows computer running Docker Desktop." }
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
  throw "Missing .env.live. Restore the existing live environment, not new database credentials."
}
if (-not $DatasetFolder) {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = "Choose the DATASET folder containing bird-species subfolders"
    $picker.ShowNewFolderButton = $false
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
      Write-Host "Cancelled: no changes made."
      return
    }
    $DatasetFolder = $picker.SelectedPath
  }
  catch {
    throw "Folder chooser unavailable. Pass -DatasetFolder 'D:\BirdDatasets\BirdGaze2026' explicitly."
  }
}
$dataset = (Resolve-Path -LiteralPath $DatasetFolder).Path
$info = Get-Item -LiteralPath $dataset -ErrorAction Stop
if (-not $info.PSIsContainer -or ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw "Choose an existing real directory, not a file, junction or shortcut."
}
if ($info.Name.StartsWith(".") -or $info.Name.Length -gt 200) {
  throw "Dataset directory name must not start with a dot or exceed 200 characters."
}
$species = @(Get-ChildItem -LiteralPath $dataset -Directory | Where-Object {
  ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
})
if ($species.Count -eq 0) {
  throw "Choose the dataset ROOT containing species subfolders, e.g. BirdGaze2026\CorvusCorone\video.mp4."
}
$parent = Split-Path -Path $dataset -Parent
if (-not $parent) { throw "Cannot use a drive root as the dataset directory." }
$dockerParent = $parent.Replace("\", "/")
$setting = "NIGHTJAR_DATASET_HOST_DIR=$dockerParent"
$compose = @("--env-file", ".env.live", "-f", "compose.live.yaml")
function Check-Exit([string]$action) {
  if ($LASTEXITCODE -ne 0) { throw "$action failed (exit $LASTEXITCODE). Data and database volumes were not deleted." }
}
Push-Location $root
$previousMount = [Environment]::GetEnvironmentVariable("NIGHTJAR_DATASET_HOST_DIR", "Process")
try {
  & docker compose version | Out-Null
  Check-Exit "Docker Desktop"
  $original = [IO.File]::ReadAllText($envFile)
  $existing = [regex]::Match($original, '(?m)^NIGHTJAR_DATASET_HOST_DIR=(.*)$')
  $oldPath = if ($existing.Success) { $existing.Groups[1].Value.Trim() } else { "" }
  if ($oldPath -and $oldPath -ne "./.nightjar-source" -and
      $oldPath -ne $dockerParent -and -not $ReplaceMount) {
    throw "A different source root is already mounted ($oldPath). Replacing it can break existing linked datasets. Use -ReplaceMount only after checking existing datasets."
  }
  if ($existing.Success) {
    $updated = [regex]::Replace(
      $original, '(?m)^NIGHTJAR_DATASET_HOST_DIR=.*$',
      [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $setting }
    )
  }
  else {
    $updated = $original.TrimEnd([char[]]@(13, 10)) +
      [Environment]::NewLine + $setting + [Environment]::NewLine
  }
  $env:NIGHTJAR_DATASET_HOST_DIR = $dockerParent
  & docker compose @compose config --quiet
  Check-Exit "Docker Compose mount preflight"
  if ($updated -ne $original) {
    $backupDir = Join-Path $root "backups"
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backup = Join-Path $backupDir (".env.live.dataset-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".bak")
    [IO.File]::WriteAllText($backup, $original, [System.Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($envFile, $updated, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Source mount saved in .env.live. Existing credentials retained."
  }
  else {
    Write-Host "Source mount already configured."
  }

  $runningIds = @(& docker compose @compose --profile native ps --status running -q worker)
  Check-Exit "Inspecting Supervisely worker"
  $wasRunning = @($runningIds | Where-Object { $_ }).Count -gt 0

  # Only the media-consuming services get their read-only source bind updated.
  # Never call 'down', '--volumes', or recreate the Postgres container.
  & docker compose @compose up -d --no-deps --force-recreate api
  Check-Exit "Recreating API with new read-only source mount"
  & docker compose @compose up -d --no-deps --force-recreate indexer
  Check-Exit "Starting mounted-folder indexer"
  & docker compose @compose up -d --no-deps --force-recreate web
  Check-Exit "Refreshing web proxy"
  if ($wasRunning -or $StartWorker) {
    & docker compose @compose --profile native up -d --no-deps --force-recreate worker
    Check-Exit "Recreating source upload worker"
  }

  $apiId = (& docker compose @compose ps -q api | Select-Object -First 1)
  Check-Exit "Finding API container"
  if (-not $apiId) { throw "Nightjar API container is missing." }
  & docker exec $apiId test -d "/source-datasets/$($info.Name)"
  Check-Exit "Verifying the selected dataset is visible in Docker"

  Write-Host ""
  Write-Host "Dataset '$($info.Name)' is now visible inside Nightjar."
  Write-Host "In Nightjar: Data -> Source folders & upload -> Refresh."
  Write-Host "Select '$($info.Name)' and click 'Link folder & start Supervisely upload'."
  if (-not ($wasRunning -or $StartWorker)) {
    Write-Warning "Local folder indexing will run automatically, but Supervisely UPLOAD still requires the native worker. Start it after verifying Team/Workspace:"
    Write-Host "docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --no-deps worker"
  }
}
finally {
  [Environment]::SetEnvironmentVariable("NIGHTJAR_DATASET_HOST_DIR", $previousMount, "Process")
  Pop-Location
}
