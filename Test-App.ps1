param(
    [string]$BaseUrl = 'http://127.0.0.1:8000'
)

# Read-only local smoke test. Never sends invitations or edits event data.
$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
try {
    $Health = Invoke-RestMethod -Method Get -Uri ($BaseUrl + '/healthz') -TimeoutSec 10
    if ($Health.status -ne 'ok' -or $Health.mode -ne 'local') {
        throw ("Unexpected app health or mode: {0}" -f ($Health | ConvertTo-Json -Compress))
    }
    $Page = Invoke-WebRequest -Uri ($BaseUrl + '/') -UseBasicParsing -TimeoutSec 10
    if ($Page.StatusCode -ne 200 -or $Page.Content -notmatch 'nightjar-dashboard') {
        throw 'The local HTML shell did not render the app dashboard.'
    }
    $Vue = Invoke-WebRequest -Uri ($BaseUrl + '/static/vendor/vue.min.js') -UseBasicParsing -TimeoutSec 10
    if ($Vue.StatusCode -ne 200 -or $Vue.Content.Length -lt 10000) {
        throw 'The bundled Vue frontend did not load.'
    }
    $Invites = Invoke-WebRequest -Uri ($BaseUrl + '/static/event_registrations.js') -UseBasicParsing -TimeoutSec 10
    if ($Invites.StatusCode -ne 200 -or $Invites.Content -notmatch 'registration_invite_selected') {
        throw 'The latest selected-user invitation UI is not being served.'
    }
    Write-Host 'PASS: healthy local database, HTML shell, Vue assets, and selected-user invitations.'
    Write-Host ("Open {0} to sign in. This test sent no invitations." -f $BaseUrl)
} catch {
    Write-Error ("Monitoring App smoke test FAILED: {0}" -f $_.Exception.Message)
    exit 1
}
