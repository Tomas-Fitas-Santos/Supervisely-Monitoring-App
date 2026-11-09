param(
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
    Write-Host 'Starting Supervisely Monitoring App. Open http://127.0.0.1:8000 after the server is ready.'
    & $Python -m monitoring.run --local
    if ($LASTEXITCODE -ne 0) { throw 'The app stopped with an error; inspect the output above.' }
}
finally { Pop-Location }
