# Offline/local preflight only. Never starts cloudflared or changes DNS.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
  & docker compose --env-file .env.live -f compose.live.yaml -f compose.tunnel.yaml --profile tunnel config --quiet
  if ($LASTEXITCODE -ne 0) { throw "Tunnel Compose configuration is invalid." }
  $tokenFile = Join-Path $projectRoot "secrets/cloudflare-tunnel-token.txt"
  if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    Write-Host "Configuration valid. Activation still needs a private tunnel token file and a Cloudflare hostname."
  } elseif ((Get-Item -LiteralPath $tokenFile).Length -eq 0) {
    throw "The tunnel token file is empty."
  } else {
    Write-Host "Configuration valid; private token file exists. Token validity is not tested by this offline check."
  }
  Write-Host "Cloudflare hostname origin must be http://web:80. Nothing has been deployed or started."
}
finally { Pop-Location }
