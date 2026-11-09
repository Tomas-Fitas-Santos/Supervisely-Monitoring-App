param(
    [ValidateSet('Local', 'Demo')]
    [string]$Mode = 'Local',
    [switch]$Install,
    [switch]$CheckOnly
)

# Both Local and Demo are authenticated, real-account rehearsals using the
# same local persistent event database. Demo NEVER enables fake users/invites.
# Native production hosting uses Dockerfile + monitoring.release, not this
# loopback-only developer launcher.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw 'Install Python 3.12 (64-bit), then run this script again.'
        }
        $Install = $true
    }

    if (-not $Install) {
        & $Python -c 'import magic, supervisely, sqlalchemy, cryptography, openpyxl; import monitoring.run'
        if ($LASTEXITCODE -ne 0) { $Install = $true }
    }

    if ($Install) {
        & $Python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $Python -m pip install --force-reinstall --no-deps -r requirements-windows.txt
        if ($LASTEXITCODE -ne 0) { throw 'Windows libmagic installation failed.' }
    }

    & $Python -c 'import magic, supervisely, sqlalchemy, cryptography, openpyxl; import monitoring.run'
    if ($LASTEXITCODE -ne 0) {
        throw 'Application imports failed; inspect the error above.'
    }
    & $Python -c 'from pathlib import Path; p=Path("monitoring/static"); files=("local.html", "dashboard.js", "dashboard.css", "vendor/vue.min.js", "event_registrations.js"); missing=[name for name in files if not (p/name).is_file()]; assert not missing, "Missing frontend assets: "+", ".join(missing); print("Local frontend assets: OK")'
    if ($LASTEXITCODE -ne 0) { throw 'Frontend assets are missing.' }

    if ($CheckOnly) {
        Write-Host 'Local deployment prerequisites OK. No server or invitations were started.'
        return
    }

    Write-Host ("Starting Supervisely Monitoring App in {0} rehearsal mode." -f $Mode)
    Write-Host 'Open http://127.0.0.1:8000 and sign in with your personal Supervisely User ID and API token.'
    Write-Host 'The SDK can log "no-html mode"; the app local shell serves its own HTML.'
    & $Python -m monitoring.run --local
    if ($LASTEXITCODE -ne 0) {
        throw 'The app stopped with an error; inspect the output above.'
    }
}
finally {
    Pop-Location
}
