# Read-only operational preflight for the persistent Nightjar live deployment.
# Checks running services, verifies API readiness, and reports dataset source
# identity vs. uploaded images. Never modifies DB, volumes or credentials.
# Usage: .\scripts\verify-live.ps1 -RequireWorkers
[CmdletBinding()]
param([switch]$RequireWorkers)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
try {
  if (-not (Test-Path ".env.live")) { throw "Missing existing .env.live." }
  $compose = @("--env-file", ".env.live", "-f", "compose.live.yaml")
  & docker compose @compose config --quiet
  if ($LASTEXITCODE -ne 0) { throw "Invalid live Docker Compose configuration." }
  foreach ($service in @("db", "indexer", "api", "web")) {
    $ids = @(& docker compose @compose ps --status running -q $service) | Where-Object { $_ }
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) {
      throw "Expected one running $service container; found $($ids.Count)."
    }
    Write-Host "$service : running"
  }
  foreach ($service in @("worker", "mirror-worker")) {
    $ids = @(& docker compose @compose --profile native ps --status running -q $service) | Where-Object { $_ }
    if ($LASTEXITCODE -ne 0) { throw "Failed to inspect $service state." }
    if ($RequireWorkers -and $ids.Count -ne 1) {
      throw "Expected one running $service container. The Supervisely SDK job/upload lanes are not ready."
    }
    Write-Host "$service : $(if ($ids.Count -eq 1) {'running'} else {'stopped'})"
  }
  $environment = Get-Content ".env.live" -Raw
  $portMatch = [regex]::Match($environment, '(?m)^NIGHTJAR_LIVE_TEST_PORT=([0-9]+)')
  $port = if ($portMatch.Success) { $portMatch.Groups[1].Value } else { "8080" }
  & curl.exe --fail --silent --show-error "http://127.0.0.1:$port/health/ready"
  if ($LASTEXITCODE -ne 0) { throw "Nightjar HTTP readiness failed on port $port." }
  Write-Host ""
  Write-Host "Live dataset/source health (read-only):"
  $audit = @'
import os

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from annotatiathon.models import MediaAsset, MediaDataset, MediaVideo
from annotatiathon.native_media_pool import mirror_status

# The live Compose stack provides POSTGRES_* via .env.live, but some
# installations do not define DATABASE_URL in the API container. Build a
# private connection from the same settings as the database service.
# URL.create safely escapes special characters in passwords.
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    values = {key: os.environ.get(key) for key in (
        "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"
    )}
    if not all(values.values()):
        raise SystemExit("Database audit configuration missing. Expected DATABASE_URL "
                         "or POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB.")
    database_url = URL.create(
        "postgresql+psycopg", username=values["POSTGRES_USER"],
        password=values["POSTGRES_PASSWORD"], host="db",
        database=values["POSTGRES_DB"],
    )
audit_engine = create_engine(database_url, pool_pre_ping=True)
with Session(audit_engine) as db:
    # The audit must never modify event data, even if extended later.
    db.execute(text("SET TRANSACTION READ ONLY"))
    datasets = db.scalars(select(MediaDataset).order_by(MediaDataset.created_at)).all()
    for ds in datasets:
        if ds.status != "ready":
            print(f"{ds.name}: import_status={ds.status}, no distribution readiness claimed")
            continue
        scope = (MediaAsset.dataset_id == ds.id,)
        frames = db.scalar(select(func.count(MediaAsset.id)).where(*scope)) or 0
        unlinked = db.scalar(select(func.count(MediaAsset.id)).where(
            *scope, MediaAsset.video_id.is_(None))) or 0
        missing_positions = db.scalar(select(func.count(MediaAsset.id)).where(
            *scope, MediaAsset.frame_index.is_(None))) or 0
        invalid_videos = db.scalar(select(func.count(MediaVideo.id)).where(
            MediaVideo.dataset_id == ds.id,
            (MediaVideo.identity_status != "complete") |
            (~MediaVideo.density.in_(("full", "declared_full"))))) or 0
        mirror = mirror_status(db, ds)
        print(f"{ds.name}: frames={frames}, source_unlinked={unlinked}, "
              f"frame_position_missing={missing_positions}, invalid_videos={invalid_videos}, "
              f"mirror={mirror['uploaded_frames']}/{mirror['total_frames']} "
              f"({mirror['state']})")
        if unlinked or missing_positions or invalid_videos:
            print("  WARNING: mirror completion does NOT make this dataset eligible "
                  "for shared 30-frame jobs. Use Automatic distribution > Check source frames.")
'@
  $audit | & docker compose @compose exec -T api python -
  if ($LASTEXITCODE -ne 0) { throw "Read-only Nightjar source audit failed." }
  Write-Host "Live preflight completed. No records or files modified."
}
finally {
  Pop-Location
}
