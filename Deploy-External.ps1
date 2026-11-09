param(
    [switch]$Up,
    [switch]$Logs
)

# Validate the existing external HTTPS deployment. -Up is an explicit Docker
# deployment action; without it this script performs only read-only preflight.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $file = Join-Path $PSScriptRoot '.env.external'
    if (-not (Test-Path $file)) {
        throw 'Create .env.external from .env.external.example and fill the real values.'
    }
    $values = @{}
    foreach ($line in (Get-Content $file)) {
        if ($line -match '^\s*([A-Z][A-Z0-9_]*)=(.*)$') {
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$Matches[1]] = $value
        }
    }

    $required = @(
        'APP_DOMAIN', 'EXTERNAL_PUBLIC_ORIGIN', 'SERVER_ADDRESS',
        'EXTERNAL_TEAM_ID', 'EXTERNAL_ORGANISER_EMAIL',
        'SUPERVISELY_SERVICE_TOKEN', 'EXTERNAL_AUTH_SECRET',
        'POSTGRES_PASSWORD', 'DATABASE_URL', 'SMTP_HOST', 'SMTP_FROM'
    )
    foreach ($name in $required) {
        $v = [string]$values[$name]
        if ([string]::IsNullOrWhiteSpace($v) -or $v -match '(?i)REPLACE_WITH|your-domain|your-provider|example\.org|\.example$') {
            throw ("Configure {0} in .env.external before deployment." -f $name)
        }
    }
    if ($values.APP_DOMAIN -notmatch '^[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$' -or
        $values.APP_DOMAIN -notmatch '\.') {
        throw 'APP_DOMAIN must be a public DNS hostname, not a URL.'
    }
    if ($values.EXTERNAL_PUBLIC_ORIGIN -ne ('https://' + $values.APP_DOMAIN)) {
        throw 'EXTERNAL_PUBLIC_ORIGIN must match https://APP_DOMAIN exactly.'
    }
    if ($values.EXTERNAL_TEAM_ID -notmatch '^[1-9][0-9]*$') {
        throw 'EXTERNAL_TEAM_ID must be the existing positive numeric Supervisely team ID.'
    }
    if ($values.EXTERNAL_ORGANISER_EMAIL -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') {
        throw 'EXTERNAL_ORGANISER_EMAIL must be a valid event organiser email.'
    }
    if ($values.SERVER_ADDRESS -notmatch '^https://[^\s/]+/?$') {
        throw 'SERVER_ADDRESS must be the base HTTPS Supervisely server URL.'
    }
    if ($values.EXTERNAL_AUTH_SECRET.Length -lt 32) {
        throw 'EXTERNAL_AUTH_SECRET must contain at least 32 random characters.'
    }
    if ($values.POSTGRES_PASSWORD -notmatch '^[a-zA-Z0-9]{24,}$') {
        throw 'Use a random PostgreSQL password of at least 24 alphanumeric characters.'
    }
    $expectedDatabaseUrl = 'postgresql+psycopg://monitor:' + $values.POSTGRES_PASSWORD + '@database:5432/monitor'
    if ($values.DATABASE_URL -ne $expectedDatabaseUrl) {
        throw 'DATABASE_URL must use the same PostgreSQL password and internal database host.'
    }
    if ([bool]$values.SMTP_USER -ne [bool]$values.SMTP_PASSWORD) {
        throw 'Set both SMTP_USER and SMTP_PASSWORD, or neither for an SMTP service permitting anonymous delivery.'
    }
    if ($values.SMTP_PORT -and $values.SMTP_PORT -notin @('465','587')) {
        throw 'SMTP_PORT must be 465 or 587.'
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install Docker with Compose plugin on the deployment server.'
    }
    & docker compose --env-file .env.external -f compose.external.yaml config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose configuration failed; inspect .env.external without sharing its secrets.'
    }
    Write-Host 'PASS: External HTTPS deployment configuration is consistent.'
    Write-Host ("Expected public application URL: {0}" -f $values.EXTERNAL_PUBLIC_ORIGIN)
    Write-Host 'No invitation, account, database or remote operation was performed.'
    if ($Up) {
        & docker compose --env-file .env.external -f compose.external.yaml up -d --build
        if ($LASTEXITCODE -ne 0) { throw 'Docker Compose deployment failed.' }
        Write-Host 'Containers started. Verify DNS, HTTPS, SMTP delivery, health and monitor sign-in.'
    }
    if ($Logs) {
        & docker compose --env-file .env.external -f compose.external.yaml logs --tail 50 monitoring
        if ($LASTEXITCODE -ne 0) { throw 'Could not read monitoring container logs.' }
    }
} finally {
    Pop-Location
}
