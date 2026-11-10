"""Auditable, read-only Supervisely annotation export with source provenance.

A worker writes a ZIP atomically. The archive contains one snapshot of current
native annotation JSON per copied pair job image, plus curated acceptance metadata.
This does NOT claim to contain Supervisely historical annotation versions.
"""
import json
import os
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    Event,
    JobReview,
    LabelingJob,
    MediaAsset,
    NativeJobBinding,
    NativeMediaInstance,
    NativeOperation,
    epoch,
)

router = APIRouter(prefix="/api/v1/native/export", tags=["native annotation export"])


def archive_path(event_id: str) -> Path:
    # event_id is server-owned, never taken directly from a URL path parameter.
    return Path(get_settings().local_data_dir).resolve() / "exports" / (
        "nightjar-native-" + event_id + ".zip"
    )


@router.get("")
def export_status(
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.event_id == event.id,
        NativeOperation.kind == "export_event",
        NativeOperation.target_id == event.id,
    ))
    exists = archive_path(event.id).is_file() if op and op.status == "done" else False
    return {
        "event_id": event.id,
        "operation_id": op.id if op else None,
        "status": op.status if op else "not_requested",
        "error_code": op.error_code if op else None,
        "attempts": op.attempts if op else 0,
        "ready_to_download": bool(exists),
        "size_bytes": archive_path(event.id).stat().st_size if exists else None,
    }


@router.post("/prepare", status_code=202)
def prepare_export(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    if event.status != "archived":
        raise HTTPException(
            status_code=409, detail="Archive/close the event before exporting native annotations."
        )
    has_images = db.scalar(select(NativeMediaInstance.id).where(
        NativeMediaInstance.event_id == event.id
    ))
    if not has_images:
        raise HTTPException(
            status_code=409,
            detail="No native media IDs recorded. No Supervisely annotations can be exported.",
        )
    existing = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "export_event",
        NativeOperation.target_id == event.id,
    ))
    if existing:
        return {"operation_id": existing.id, "status": existing.status}
    task = NativeOperation(
        event_id=event.id, kind="export_event", target_id=event.id,
        status="pending",
    )
    db.add(task)
    db.flush()
    db.add(AuditEvent(
        event_id=event.id, staff_id=ctx.staff.id,
        action="native.export.prepare", target_type="native_operation",
        target_id=task.id, details={"current_annotations_only": True},
    ))
    db.commit()
    return {"operation_id": task.id, "status": task.status}


@router.post("/retry")
def retry_export(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.event_id == event.id,
        NativeOperation.kind == "export_event",
        NativeOperation.target_id == event.id,
    ).with_for_update())
    if op is None:
        raise HTTPException(status_code=404, detail="Export not requested.")
    if op.status not in {"attention", "blocked"}:
        raise HTTPException(status_code=409, detail="Only failed exports can be retried.")
    op.status = "pending"
    op.error_code = None
    op.claimed_at = None
    db.add(AuditEvent(
        event_id=event.id, staff_id=ctx.staff.id,
        action="native.export.retry", target_type="native_operation",
        target_id=op.id, details={"attempts": op.attempts},
    ))
    db.commit()
    return {"operation_id": op.id, "status": op.status}


@router.get("/download")
def download_export(
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    task = db.scalar(select(NativeOperation).where(
        NativeOperation.event_id == event.id,
        NativeOperation.kind == "export_event",
        NativeOperation.target_id == event.id,
        NativeOperation.status == "done",
    ))
    path = archive_path(event.id)
    if task is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Native export is not ready.")
    return FileResponse(
        path, media_type="application/zip", filename=path.name,
        headers={"Cache-Control": "private, no-store"},
    )


def export_event_zip(db: Session, api, event_id: str) -> dict:
    """Write current native annotation JSON and full/curated source manifests."""
    event = db.get(Event, event_id)
    if event is None or event.status != "archived":
        raise ValueError("event_not_closed")
    path = archive_path(event_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    full_index = path.with_name(path.name + ".full.jsonl.partial")
    curated_index = path.with_name(path.name + ".curated.jsonl.partial")
    for partial in (temporary, full_index, curated_index):
        partial.unlink(missing_ok=True)
    total = 0
    curated = 0
    try:
        with (
            full_index.open("w", encoding="utf-8") as manifest,
            curated_index.open("w", encoding="utf-8") as curated_manifest,
            zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=5, allowZip64=True,
            ) as zipped,
        ):
            offset = 0
            while True:
                media_copies = list(db.scalars(select(NativeMediaInstance).where(
                    NativeMediaInstance.event_id == event_id,
                ).order_by(
                    NativeMediaInstance.job_id, NativeMediaInstance.media_id,
                ).limit(200).offset(offset)))
                if not media_copies:
                    break
                offset += len(media_copies)
                for mapping in media_copies:
                    job = db.get(LabelingJob, mapping.job_id)
                    asset = db.get(MediaAsset, mapping.media_id)
                    if (job is None or asset is None
                            or job.event_id != event_id or asset.event_id != event_id
                            or mapping.native_dataset_id != job.native_dataset_id):
                        raise ValueError("export_provenance_mismatch")
                    native_bindings = list(db.scalars(select(NativeJobBinding).where(
                        NativeJobBinding.event_id == event_id,
                        NativeJobBinding.job_id == job.id,
                    )))
                    if len(native_bindings) != 2:
                        raise ValueError("unbound_native_job")
                    review = db.scalar(select(JobReview).where(
                        JobReview.event_id == event_id,
                        JobReview.job_id == job.id,
                        JobReview.media_id == asset.id,
                    ))
                    annotation = api.annotation.download_json(
                        mapping.native_image_id
                    )
                    if not isinstance(annotation, dict):
                        raise ValueError("invalid_native_annotation")
                    record = {
                        "event_id": event_id, "team_id": job.team_id,
                        "job_id": job.id, "job_sequence": job.sequence,
                        "source_dataset_id": job.dataset_id,
                        "source_media_id": asset.id,
                        "relative_path": asset.relative_path,
                        "source_sha256": asset.sha256,
                        "morphology": asset.morphology,
                        "video_group": asset.video_group,
                        "native_dataset_id": mapping.native_dataset_id,
                        "native_image_id": mapping.native_image_id,
                        "native_job_ids": sorted(
                            b.native_job_id for b in native_bindings
                        ),
                        "annotation_state": "current_snapshot",
                        "job_status": job.status,
                        "review_status": job.review_status,
                        "frame_verdict": review.verdict if review else None,
                        "frame_sampled": review is not None,
                        "captured_at": epoch(),
                    }
                    filename = "annotations/" + job.id + "/" + asset.id + ".json"
                    zipped.writestr(
                        filename,
                        json.dumps(annotation, separators=(",", ":"), ensure_ascii=False),
                    )
                    manifest.write(
                        json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
                    )
                    total += 1
                    if (job.status == "completed" and job.review_status == "accepted"
                            and (review is None or review.verdict == "good")):
                        curated_manifest.write(
                            json.dumps(
                                record, separators=(",", ":"), ensure_ascii=False,
                            ) + "\n"
                        )
                        curated += 1
            manifest.flush()
            curated_manifest.flush()
            zipped.write(full_index, "full_manifest.jsonl")
            zipped.write(curated_index, "curated_manifest.jsonl")
            zipped.writestr("README.json", json.dumps({
                "schema": "nightjar-native-annotations-v1",
                "event_id": event_id,
                "exported_at": epoch(),
                "total_copies": total,
                "curated_copies": curated,
                "annotation_state": "current_snapshot_only",
                "notes": [
                    "Media bytes remain in the private local dataset directory.",
                    "Overlapping pair jobs have independent native image IDs.",
                    "Each pair stores two annotator job IDs over the same pair dataset.",
                    "Historical Supervisely annotation versions are not included.",
                    "Curated rows require an accepted job and no explicitly bad frame verdict.",
                ],
            }, indent=2))
        os.replace(temporary, path)
    except BaseException:  # noqa: BLE001 - cleanup even for interrupted exports
        temporary.unlink(missing_ok=True)
        raise
    finally:
        full_index.unlink(missing_ok=True)
        curated_index.unlink(missing_ok=True)
    return {
        "event_id": event_id, "total_copies": total,
        "curated_copies": curated, "bytes": path.stat().st_size,
    }
