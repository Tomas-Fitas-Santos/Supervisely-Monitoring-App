# Schema-compatible API/frontend release without stopping the serving stack.
# Run backup-live.ps1 first. This does not migrate the database or restart workers.
[CmdletBinding()]
param([switch]$SkipBuild)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
  $composeArgs = @("--env-file", ".env.live", "-f", "compose.live.yaml")
  function Check-HotCommand([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action failed with exit code $LASTEXITCODE." }
  }
  function Write-Utf8([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
  }
  function Get-ServiceContainer([string]$Service) {
    $containers = @(& docker compose @composeArgs ps -q $Service)
    if ($LASTEXITCODE -ne 0) { throw "Finding $Service failed with exit code $LASTEXITCODE." }
    $container = $containers | Select-Object -First 1
    if (-not $container) { throw "The existing $Service service must already be running." }
    return $container
  }
  $servingWeb = Get-ServiceContainer "web"
  $servingApi = Get-ServiceContainer "api"
  $releaseId = Get-Date -Format "yyyyMMdd-HHmmss"
  $releaseDir = Join-Path $projectRoot "backups/deployments/hot-$releaseId"
  New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
  $candidateApi = "nightjar-live-api-$releaseId"
  $assetContainer = "nightjar-live-assets-$releaseId"
  $apiImage = "nightjar-release/api:$releaseId"
  $webImage = "nightjar-release/web:$releaseId"
  $oldIndex = Join-Path $releaseDir "previous-index.html"
  $oldConfig = Join-Path $releaseDir "previous-nginx.conf"
  $nextConfig = Join-Path $releaseDir "next-nginx.conf"
  $manifestPath = Join-Path $releaseDir "release.json"
  $indexPublished = $false
  $assetContainerCreated = $false

  & docker cp "${servingWeb}:/etc/nginx/conf.d/default.conf" $oldConfig
  Check-HotCommand "Saving the serving Nginx configuration"
  & docker cp "${servingWeb}:/usr/share/nginx/html/index.html" $oldIndex
  Check-HotCommand "Saving the serving frontend entrypoint"
  $configText = [IO.File]::ReadAllText($oldConfig)
  $upstreamPattern = 'proxy_pass\s+http://[A-Za-z0-9_.-]+:8000;'
  if ([regex]::Matches($configText, $upstreamPattern).Count -ne 1) {
    throw "Expected one private API upstream; the serving configuration was not changed."
  }
  $oldUpstream = [regex]::Match($configText, $upstreamPattern).Value
  foreach ($serviceContainer in @(@{kind="api"; id=$servingApi}, @{kind="web"; id=$servingWeb})) {
    $previousImage = (& docker inspect --format '{{.Image}}' $serviceContainer.id)
    Check-HotCommand "Reading the previous $($serviceContainer.kind) image"
    $rollbackImage = "nightjar-rollback/hot-$($serviceContainer.kind):$releaseId"
    & docker image inspect --format '{{.Id}}' $previousImage 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      & docker image tag $previousImage $rollbackImage
      Check-HotCommand "Retaining the previous $($serviceContainer.kind) image"
    } else {
      # A running container can outlive an image whose layers were pruned.
      # Keep that container serving/draining and preserve its application files.
      $previousFiles = if ($serviceContainer.kind -eq "api") { "/app/src/annotatiathon" } else { "/usr/share/nginx/html" }
      & docker cp "$($serviceContainer.id):$previousFiles" (Join-Path $releaseDir "previous-$($serviceContainer.kind)")
      Check-HotCommand "Saving previous $($serviceContainer.kind) application files"
      Write-Warning "Previous image layers are unavailable; the existing container is retained for rollback."
    }
  }
  $schemaHash = @{}
  Get-ChildItem (Join-Path $projectRoot "migrations/versions") -File -Filter "*.py" | ForEach-Object {
    $schemaHash[$_.Name] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  $schemaReader = @'
import hashlib,json
from pathlib import Path
print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/migrations/versions').glob('*.py')}))
'@
  $liveSchemaJson = $schemaReader | & docker exec -i $servingApi /usr/local/bin/nightjar-entrypoint python -
  Check-HotCommand "Checking deployed migration files"
  $liveSchema = $liveSchemaJson | ConvertFrom-Json -AsHashtable
  if ($schemaHash.Count -ne $liveSchema.Count -or @($schemaHash.Keys | Where-Object {
    $schemaHash[$_] -ne $liveSchema[$_]
  }).Count) {
    throw "Migration files differ. Use the full migration-aware deployment workflow."
  }
  if (-not $SkipBuild) {
    & docker compose @composeArgs build api web
    Check-HotCommand "Building the API and frontend while the site remains online"
  }
  & docker image tag nightjar-live-api:latest $apiImage
  Check-HotCommand "Pinning the candidate API image"
  & docker image tag nightjar-live-web:latest $webImage
  Check-HotCommand "Pinning the candidate frontend image"
  $manifest = [ordered]@{
    release = $releaseId; mode = "schema-compatible-hot"; deployed = $false
    serving_web = $servingWeb; previous_api = $servingApi; previous_upstream = $oldUpstream
    candidate_api = $candidateApi; api_image = $apiImage; web_image = $webImage
    previous_index = $oldIndex; previous_nginx = $oldConfig
    workers_restarted = $false; database_migrated = $false
  }
  $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8
  try {
    # Compose supplies the same private network, mounted media and encrypted
    # configuration. No service aliases or host ports are assigned to this API.
    & docker compose @composeArgs run -d --no-deps --name $candidateApi api
    Check-HotCommand "Starting the candidate API beside the serving API"
    & docker update --restart unless-stopped $candidateApi | Out-Null
    Check-HotCommand "Keeping the candidate API persistent across host restarts"
    $healthy = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
      $candidateHealth = (& docker inspect --format '{{.State.Health.Status}}' $candidateApi)
      Check-HotCommand "Checking candidate API readiness"
      if ($candidateHealth -eq "healthy") { $healthy = $true; break }
      if ((& docker inspect --format '{{.State.Status}}' $candidateApi) -ne "running") { break }
      Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw "Candidate API did not become healthy; serving API is still active." }
    & docker exec $servingWeb wget -q -O /dev/null "http://${candidateApi}:8000/health/ready"
    Check-HotCommand "Verifying private candidate connectivity from Nginx"

    & docker create --name $assetContainer $webImage | Out-Null
    Check-HotCommand "Opening the candidate frontend image"
    $assetContainerCreated = $true
    & docker cp "${assetContainer}:/usr/share/nginx/html" (Join-Path $releaseDir "frontend")
    Check-HotCommand "Extracting the candidate frontend"
    # Keep previous hashed assets available to staff with already-open tabs.
    & docker cp (Join-Path $releaseDir "frontend/assets/.") "${servingWeb}:/usr/share/nginx/html/assets/"
    Check-HotCommand "Publishing new frontend assets without removing old assets"
    & docker cp (Join-Path $releaseDir "frontend/index.html") "${servingWeb}:/usr/share/nginx/html/.nightjar-next-index"
    Check-HotCommand "Staging the frontend entrypoint"
    Write-Utf8 $nextConfig ([regex]::Replace($configText, $upstreamPattern,
      "proxy_pass http://${candidateApi}:8000;"))
    & docker cp $nextConfig "${servingWeb}:/etc/nginx/conf.d/default.conf"
    Check-HotCommand "Staging the healthy private API upstream"
    & docker exec $servingWeb nginx -t
    Check-HotCommand "Validating Nginx before switching traffic"
    & docker exec $servingWeb nginx -s reload
    Check-HotCommand "Gracefully switching API traffic"
    & docker exec $servingWeb mv /usr/share/nginx/html/.nightjar-next-index /usr/share/nginx/html/index.html
    Check-HotCommand "Atomically publishing the frontend entrypoint"
    $indexPublished = $true
    $environmentText = Get-Content ".env.live" -Raw
    $portMatch = [regex]::Match($environmentText, '(?m)^NIGHTJAR_LIVE_TEST_PORT=([0-9]+)')
    $healthPort = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { "8080" }
    & curl.exe --fail --silent --show-error "http://127.0.0.1:$healthPort/health/ready"
    Check-HotCommand "Verifying the serving combined origin"
    Write-Host ""
    $manifest.deployed = $true
    $manifest.completed_utc = [DateTime]::UtcNow.ToString("o")
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Write-Host "Hot release $releaseId is serving. Existing API retained for draining/rollback."
    Write-Host "Database, workers, web container and tunnel were not stopped."
    Write-Host "Release receipt: $manifestPath"
  }
  catch {
    # Restore configuration even when validation failed before the reload.
    & docker cp $oldConfig "${servingWeb}:/etc/nginx/conf.d/default.conf"
    if ($LASTEXITCODE -eq 0) {
      & docker exec $servingWeb nginx -t
      if ($LASTEXITCODE -eq 0) { & docker exec $servingWeb nginx -s reload }
    }
    if ($indexPublished) {
      & docker cp $oldIndex "${servingWeb}:/usr/share/nginx/html/.nightjar-rollback-index"
      if ($LASTEXITCODE -eq 0) {
        & docker exec $servingWeb mv /usr/share/nginx/html/.nightjar-rollback-index /usr/share/nginx/html/index.html
      }
    }
    Write-Warning "Hot release failed; restored the previous Nginx configuration and frontend entrypoint."
    throw
  }
  finally {
    if ($assetContainerCreated) { & docker rm $assetContainer | Out-Null }
  }
}
finally { Pop-Location }
