# Nightjar forensic report v3; all SQL and Supervisely operations are read-only.
# Run from the Supervisely-Monitoring-App repository root:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\nightjar_readonly_diagnosis_v3.ps1
# Optional native labeling-job ID (NOT the Supervisely user ID):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\nightjar_readonly_diagnosis_v3.ps1 -RemoteJobId 123456
param([long]$RemoteJobId = 0)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$compose = @('compose', '--env-file', '.env.live', '-f', 'compose.live.yaml')
Write-Host '=== Local repository revision (NOT proof of deployed image revision) ==='
& git rev-parse HEAD
Write-Host '=== Reference commit ==='
& git rev-parse 4a373bd
Write-Host '=== Running Docker services (read-only) ==='
& docker @compose ps
if ($LASTEXITCODE -ne 0) { throw 'Could not list compose containers' }
$workerIds = @(& docker @compose ps -q worker | Where-Object { $_ -and $_.Trim() })
if ($workerIds.Count -eq 0) { throw 'No running worker in compose.live.yaml; please provide docker compose ps output' }
Write-Host '=== Actual running worker images, code fingerprints and SDK versions ==='
foreach ($id in $workerIds) {
  & docker inspect --format '{{.Name}} image={{.Image}} status={{.State.Status}}' $id
  & docker exec $id python -c 'import hashlib,importlib.metadata as m;from pathlib import Path;import annotatiathon.native_worker as w;print("sdk="+m.version("supervisely")+" worker_sha256="+hashlib.sha256(Path(w.__file__).read_bytes()).hexdigest())'
  if ($LASTEXITCODE -ne 0) { throw 'Read-only worker fingerprint failed' }
}
Write-Host '=== Read-only SQL and Supervisely three-way lookup on first running worker ==='
$DiagnosticPython = @'
"""Read-only Nightjar/Supervisely production diagnostics.

Run INSIDE the existing Nightjar native worker container. No credentials,
annotation payloads, raw environment variables, or SQL writes are printed.
Pass an optional Supervisely labeling-job ID as the first argument.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import logging
import os
import sys
from urllib.parse import quote

# docker exec bypasses /usr/local/bin/nightjar-entrypoint, which normally
# assembles DATABASE_URL from the container's POSTGRES_* variables. Rebuild
# the same value only in this ephemeral diagnostic process. Never print it.
if not os.environ.get("DATABASE_URL"):
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    if not all(os.environ.get(key) for key in required):
        raise RuntimeError("missing_container_database_environment")
    user, password, name = (quote(os.environ[key], safe="") for key in required)
    os.environ["DATABASE_URL"] = f"postgresql+psycopg://{user}:{password}@db:5432/{name}"

from sqlalchemy import select, text

from annotatiathon.db import session_factory
from annotatiathon.models import (
    CompetitionTeam,
    LabelingJob,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeOperation,
    Person,
    SuperviselyConfig,
)
from annotatiathon.native_names import labeling_job_name, numbered_job_name
from annotatiathon.native_worker import connect_native, job_video_group
import annotatiathon.native_worker as native_worker

# Suppress SDK diagnostics that might contain server connection details.
logging.disable(logging.CRITICAL)

OP_IDS = (
    "261e373c-ec7f-4a57-ad2f-66fb38ab95e3",
    "30f8e0a0-07d9-4023-a88e-7eb083ee858a",
    "8f39776b-1050-4c51-ae02-15a05fff6019",
    "ee0f997d-e8d6-489b-a8fe-21719a0272b7",
)
REMOTE_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 0


def emit(section, info):
    print(json.dumps({"section": section, "data": info}, ensure_ascii=False, default=str))


def safe_job(obj):
    if obj is None:
        return None
    return {
        "id": obj.id,
        "title": obj.name,
        "assignee_id": getattr(obj, "assigned_to_id", None),
        "dataset_id": getattr(obj, "dataset_id", None),
        "status": getattr(obj, "status", None),
        "images": getattr(obj, "images_count", None),
        "finished_images": getattr(obj, "finished_images_count", None),
    }


def remote_read(label, callback):
    try:
        return list(callback()) if label != "direct" else callback()
    except Exception as exc:
        # Deliberately no str(exc) or stack trace: some SDK exceptions include URLs.
        emit("read_error", {"lookup": label, "type": type(exc).__name__})
        return None


def run():
    source = inspect.getsource(native_worker.publish_job)
    runtime_path = native_worker.__file__
    emit("runtime", {
        "native_worker_module": runtime_path,
        "native_worker_sha256": hashlib.sha256(open(runtime_path, "rb").read()).hexdigest(),
        "supervisely_sdk_version": importlib.metadata.version("supervisely"),
        "uses_scoped_job_list": "list_assigned_native_jobs(" in source,
        "create_result_captured": any(
            "= api.labeling_job.create(" in line for line in source.splitlines()
        ),
    })

    with session_factory()() as db:
        # SET is transaction-local; every subsequent SQL statement is read only.
        db.execute(text("SET TRANSACTION READ ONLY"))
        config = db.get(SuperviselyConfig, 1)
        if config is None:
            emit("error", {"type": "MissingSuperviselyConfig"})
            return
        emit("team", {"team_id": config.team_id})
        local = []
        for opid in OP_IDS:
            op = db.get(NativeOperation, opid)
            if op is None:
                emit("missing_operation", {"operation_id": opid})
                continue
            job = db.get(LabelingJob, op.target_id)
            if job is None:
                emit("missing_job", {"operation_id": opid, "job_id": op.target_id})
                continue
            person = db.get(Person, job.assignee_person_id) if job.assignee_person_id else None
            team = db.get(CompetitionTeam, job.team_id)
            dataset = db.get(MediaDataset, job.dataset_id)
            bindings = list(db.scalars(select(NativeJobBinding).where(
                NativeJobBinding.job_id == job.id,
            )))
            by_id = {a.id: (a.video_group, a.frame_index) for a in db.scalars(
                select(MediaAsset).where(
                    MediaAsset.event_id == job.event_id,
                    MediaAsset.dataset_id == job.dataset_id,
                    MediaAsset.id.in_(job.media_ids),
                )
            )}
            parts = [by_id[mid] for mid in job.media_ids if mid in by_id and by_id[mid][0]]
            legacy = (labeling_job_name(
                job, team, job_video_group(db, job),
                person.name if person else "Team pair",
                source_parts=parts or None,
            ) if team and person else None)
            expected = numbered_job_name(job.native_job_number) if job.native_job_number else None
            row = {
                "operation_id": op.id,
                "kind": op.kind,
                "operation_status": op.status,
                "error_code": op.error_code,
                "attempts": op.attempts,
                "nightjar_job_id": job.id,
                "nightjar_job_status": job.status,
                "release_mode": job.release_mode,
                "native_job_number": job.native_job_number,
                "native_dataset_id": job.native_dataset_id,
                "source_dataset_native_dataset_id": dataset.native_dataset_id if dataset else None,
                "source_dataset_native_project_id": dataset.native_project_id if dataset else None,
                "expected_person_id": job.assignee_person_id,
                "expected_assignee_id": person.supervisely_user_id if person else None,
                "is_luis_124380": bool(person and person.supervisely_user_id == 124380),
                "expected_title": expected,
                "legacy_title": legacy,
                "frame_count": job.frame_count,
                "saved_native_bindings": [
                    {"native_job_id": b.native_job_id, "person_id": b.person_id,
                     "native_status": b.native_status} for b in bindings
                ],
            }
            local.append(row)
            emit("local", row)

        try:
            api = connect_native(config)  # Uses the existing encrypted configuration.
        except Exception as exc:
            emit("connection_error", {"type": type(exc).__name__})
            return

        datasets = sorted({int(row["native_dataset_id"]) for row in local
                           if row["native_dataset_id"] is not None})
        dataset_cache = {}
        for dsid in datasets:
            dataset_cache[dsid] = remote_read(
                "dataset_only",
                lambda dsid=dsid: api.labeling_job.get_list(
                    config.team_id, dataset_id=dsid, show_disabled=True,
                ),
            )
            emit("dataset_list", {
                "dataset_id": dsid,
                "total_count": (len(dataset_cache[dsid])
                                if dataset_cache[dsid] is not None else None),
            })

        direct_ids = {REMOTE_ID} if REMOTE_ID else set()
        for row in local:
            dsid, userid = row["native_dataset_id"], row["expected_assignee_id"]
            if dsid is None or userid is None:
                emit("lookup_skipped", {"nightjar_job_id": row["nightjar_job_id"],
                                        "reason": "missing_dataset_or_assignee"})
                continue
            exact = remote_read(
                "worker_exact_assignee_dataset",
                lambda dsid=dsid, userid=userid: api.labeling_job.get_list(
                    config.team_id, dataset_id=dsid,
                    assigned_to_id=userid, show_disabled=True,
                ),
            )
            dataset_rows = dataset_cache.get(int(dsid))
            accepted = {row["expected_title"], row["legacy_title"]} - {None}
            exact_matches = [x for x in exact or [] if x.name in accepted]
            same_assignee = [x for x in dataset_rows or [] if x.assigned_to_id == userid]
            title_matches_any_assignee = [x for x in dataset_rows or [] if x.name in accepted]
            exact_ids = {x.id for x in exact or []}
            dataset_ids = {x.id for x in dataset_rows or []}
            omitted = [x for x in same_assignee if x.id not in exact_ids]
            all_candidates = {x.id: x for x in (
                same_assignee + title_matches_any_assignee + (exact or [])
            )}
            direct_ids.update(x.id for x in all_candidates.values())
            emit("comparison", {
                "nightjar_job_id": row["nightjar_job_id"],
                "expected_assignee_id": userid,
                "expected_dataset_id": dsid,
                "accepted_titles": sorted(accepted),
                "worker_filtered_count": len(exact) if exact is not None else None,
                "worker_filtered_candidates": [safe_job(x) for x in (exact or [])[:40]],
                "worker_exact_title_matches": [safe_job(x) for x in exact_matches],
                "dataset_same_assignee_count": len(same_assignee),
                "dataset_omitted_from_worker_filter": [safe_job(x) for x in omitted[:40]],
                "dataset_title_match_any_assignee": [safe_job(x) for x in title_matches_any_assignee[:40]],
                "dataset_assignee_candidates": [safe_job(x) for x in same_assignee[:40]],
                "all_candidate_count": len(all_candidates),
                "multiple_exact_matches": len(exact_matches) > 1,
            })
        # Direct read-back confirms the remote records without modifying them.
        # Never fetch annotations, images, user tokens, or permission settings.
        for nativeid in sorted(direct_ids):
            info = remote_read(
                "direct",
                lambda nativeid=nativeid: api.labeling_job.get_info_by_id(nativeid),
            )
            emit("direct_job", safe_job(info) if info is not None else {"id": nativeid, "found": False})
        db.rollback()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Report only code locations and exception type; NEVER exception values,
        # request data, connection strings, token values, or stack locals.
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        emit("diagnostic_error", {
            "type": type(exc).__name__,
            "locations": [f"{frame.name}:{frame.lineno}" for frame in frames[-6:]],
        })
        sys.exit(1)

'@
$DiagnosticPython | & docker exec -i $workerIds[0] python - $RemoteJobId
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic returned an error; inspect its safe error type above' }
Write-Host '=== Diagnostic completed; no jobs, database records or annotations were modified ==='
