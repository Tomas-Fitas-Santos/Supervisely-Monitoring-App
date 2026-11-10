# Configure a private HTTP Basic Auth password in front of Nightjar.
# Use only over HTTPS (Cloudflare Quick Tunnel). Never place this in Git.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dir = Join-Path $root ".nightjar-live-auth"
$file = Join-Path $dir ".htpasswd"
New-Item -ItemType Directory -Path $dir -Force | Out-Null

if (Test-Path -LiteralPath $file) {
  Write-Host "Live-testing access password file already exists; leaving it unchanged."
  Write-Host "Keep your access password distinct from your Nightjar organiser password."
  exit 0
}

Write-Host "Creating a separate password for the temporary live-testing website gate."
Write-Host "This is NOT your Nightjar staff password, and it is NOT a Supervisely token."
Write-Host "When Docker prompts, enter a strong private gate password twice."
Write-Host "Username for the browser prompt will be: nightjar-gate"
# Apache htpasswd prompts inside the container; no password enters PowerShell
# history, argument lists, Git or the app database. -m generates salted APR1.
& docker run --rm -it --mount "type=bind,source=$dir,target=/auth" httpd:2.4-alpine /usr/local/apache2/bin/htpasswd -cm /auth/.htpasswd nightjar-gate
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $file)) {
  throw "Could not generate the local site password. No live tunnel should be started."
}

Write-Host "Private site-access password saved to the Git-ignored local directory."
Write-Host "Continue with Docker Compose and an ordinary Quick Tunnel (no --allowed-mail)."
