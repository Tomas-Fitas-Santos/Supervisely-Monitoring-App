param(
    [ValidateSet('Pilot', 'Demo', 'Event')]
    [string]$Mode = 'Demo',
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 (64-bit), then run this script again.' }
        $Install = $true
    }
    if (-not $Install) {
        & $Python -c 'import magic, supervisely, sqlalchemy, cryptography; import monitoring.run'
        if ($LASTEXITCODE -ne 0) { $Install = $true }
    }
    if ($Install) {
        & $Python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $Python -m pip install --force-reinstall --no-deps -r requirements-windows.txt
        if ($LASTEXITCODE -ne 0) { throw 'Windows libmagic installation failed.' }
        & $Python -c 'import magic, supervisely; import monitoring.run'
        if ($LASTEXITCODE -ne 0) { throw 'Application imports failed; inspect the error above.' }
    }
    Write-Host "Starting $Mode mode. Open http://127.0.0.1:8000 after the server is ready."
    if ($Mode -eq 'Pilot') { & $Python -m monitoring.run --pilot }
    elseif ($Mode -eq 'Demo') { & $Python -m monitoring.run --demo }
    else { & $Python -m monitoring.run }
    if ($LASTEXITCODE -ne 0) { throw 'The app stopped with an error; inspect the output above.' }
}
finally { Pop-Location }
