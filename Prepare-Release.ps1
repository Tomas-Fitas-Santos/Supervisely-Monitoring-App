param(
    [Parameter(Mandatory = $true)]
    [string]$Image,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$InstanceVersion,
    [string]$Output = 'dist/supervisely-release',
    [switch]$Build,
    [switch]$Push
)

# Package the native Supervisely app. Nothing is deployed or invited
# automatically. Push is an explicit action, never inferred from Build.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if ([string]::IsNullOrWhiteSpace($Image) -or $Image -notmatch '[:@]') {
        throw 'Supply a complete registry image tag or sha256 digest.'
    }
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $Python)) {
        throw 'Create the virtual environment first: .\Start-App.ps1 -Mode Local -Install -CheckOnly'
    }
    if ($Build -or $Push) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw 'Docker is not installed or is not available in PATH.'
        }
    }
    if ($Build) {
        Write-Host ("Building Docker image {0}" -f $Image)
        & docker build --pull -t $Image .
        if ($LASTEXITCODE -ne 0) { throw 'Docker image build failed.' }
    }
    if ($Push) {
        # A nonzero exit means the image was not published. The caller must
        # explicitly authenticate with the chosen registry first.
        & docker image inspect $Image *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Build or pull the image before pushing it.' }
        Write-Host ("Pushing Docker image {0}" -f $Image)
        & docker push $Image
        if ($LASTEXITCODE -ne 0) { throw 'Docker image push failed.' }
    }
    & $Python -m monitoring.release --image $Image --instance-version $InstanceVersion --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'Supervisely release preparation failed.' }
    $Resolved = Join-Path (Get-Location).Path $Output
    $ConfigPath = Join-Path $Resolved 'config.json'
    $Checksums = Join-Path $Resolved 'release-checksums.json'
    if (-not (Test-Path $ConfigPath) -or -not (Test-Path $Checksums)) {
        throw 'Generated release directory is incomplete.'
    }
    if (Test-Path (Join-Path $Resolved 'requirements.txt')) {
        throw 'Release sources must not contain requirements.txt beside config.json.'
    }
    $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    if ($Config.instance_version -ne $InstanceVersion -or $Config.docker_image -ne $Image) {
        throw 'Generated release config does not match the requested image/version.'
    }
    Write-Host 'Native Supervisely release sources verified.'
    Write-Host ("Release directory: {0}" -f $Resolved)
    Write-Host 'Next: make the image available to your Supervisely agent and release the generated folder using your instance app publishing workflow.'
    Write-Host 'A reachable persistent PostgreSQL database and writable upload staging volume are required for hosted sessions.'
    Write-Host 'This preparation does not claim live deployment or finished native labeling-job integration.'
}
finally {
    Pop-Location
}
