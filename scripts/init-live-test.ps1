# Generate isolated live-testing credentials once, without printing them.
# Keep .env.live PRIVATE and never recreate it if you want encrypted tokens to work.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $root ".env.live"
$template = Join-Path $root ".env.live.example"
if (Test-Path $target) {
  Write-Host "Existing .env.live preserved; no keys or passwords changed."
  exit 0
}
if (-not (Test-Path $template)) { throw "Missing .env.live.example in repository root." }

$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
  $passwordBytes = New-Object byte[] 24
  $keyBytes = New-Object byte[] 32
  $rng.GetBytes($passwordBytes)
  $rng.GetBytes($keyBytes)
  $password = [BitConverter]::ToString($passwordBytes).Replace("-", "").ToLowerInvariant()
  $encryptionKey = [Convert]::ToBase64String($keyBytes).Replace("+", "-").Replace("/", "_")
} finally {
  $rng.Dispose()
}
$content = [IO.File]::ReadAllText($template)
$content = $content.Replace("POSTGRES_PASSWORD=GENERATE_WITH_SCRIPT", "POSTGRES_PASSWORD=$password")
$content = $content.Replace("NIGHTJAR_ENCRYPTION_KEY=GENERATE_WITH_SCRIPT", "NIGHTJAR_ENCRYPTION_KEY=$encryptionKey")
[IO.File]::WriteAllText($target, $content, (New-Object System.Text.UTF8Encoding($false)))
New-Item -ItemType Directory -Path (Join-Path $root ".nightjar-live-data") -Force | Out-Null
Write-Host "Created private .env.live and isolated media directory."
Write-Host "Configure SMTP in .env.live before real monitor account registrations."
