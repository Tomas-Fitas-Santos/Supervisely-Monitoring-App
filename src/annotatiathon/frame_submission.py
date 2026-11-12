"""Complete-frame acceptance and partial-job snapshots for native submissions.

Only the source annotation JSON is transferred. Completed frames are immutable
once entered into the review pool. Incomplete frames retain their original
logical Nightjar job but can be republished as a smaller native job.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .frame_completion import frame_completion_errors
from .models import (
    LabelingJob,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeMediaInstance,
    ReviewSample,
    WorkPacket,
    epoch,
)


class FrameSubmissionError(ValueError):
    pass


def accepted_ids(db: Session, job: LabelingJob) -> set[str]:
    return set(db.scalars(select(ReviewSample.media_id).where(
        ReviewSample.job_id == job.id,
        ReviewSample.validated_complete.is_(True),
    )))


def pending_ids(db: Session, job: LabelingJob) -> list[str]:
    accepted = accepted_ids(db, job)
    return [media_id for media_id in job.media_ids if media_id not in accepted]


def capture_frame_submission(db: Session, api, job: LabelingJob, *,
                             source_round: int | None = None) -> dict:
    """Stage a job-scoped JSON snapshot of only structurally complete frames.

    Accepted snapshots track missing-frame corrections, but review eligibility
    stays closed until *every* original job frame has passed validation.
    The immutable round manifest supports crash-safe, idempotent retries.
    """
    dataset = db.get(MediaDataset, job.dataset_id)
    if dataset is None or dataset.status != "ready":
        raise FrameSubmissionError("source_dataset_unavailable")
    bindings = list(db.scalars(select(NativeJobBinding).where(
        NativeJobBinding.event_id == job.event_id,
        NativeJobBinding.job_id == job.id,
    )))
    if len(bindings) != 1 or bindings[0].person_id != job.assignee_person_id:
        raise FrameSubmissionError("native_job_binding_mismatch")
    source = {item.id: item for item in db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == job.event_id, MediaAsset.dataset_id == job.dataset_id,
        MediaAsset.id.in_(job.media_ids),
    ))}
    mapping = {item.media_id: item for item in db.scalars(select(NativeMediaInstance).where(
        NativeMediaInstance.event_id == job.event_id,
        NativeMediaInstance.job_id == job.id,
    ))}
    if set(source) != set(job.media_ids) or set(mapping) != set(job.media_ids):
        raise FrameSubmissionError("native_source_mapping_incomplete")
    native_job_id = bindings[0].native_job_id
    round_number = job.correction_round if source_round is None else source_round
    round_dir = Path(dataset.root_path) / "annotations" / job.id / f"round-{round_number}"
    manifest = round_dir / "manifest.json"
    if not manifest.is_file():
        remaining = pending_ids(db, job)
        if not remaining:
            return {"accepted": [], "pending": []}
        expected_native = [mapping[mid].native_image_id for mid in remaining]
        image_infos = api.image.get_list(
            job.native_dataset_id,
            filters=[{"field": "id", "operator": "in", "value": expected_native}],
        )
        by_id = {item.id: item for item in image_infos}
        if set(by_id) != set(expected_native):
            raise FrameSubmissionError("native_subset_images_missing")
        project_meta = api.labeling_job.get_project_meta(native_job_id)
        downloaded = api.labeling_job.get_annotations(
            native_job_id,
            image_infos=[by_id[image_id] for image_id in expected_native],
            project_meta=project_meta,
        )
        if len(downloaded) != len(remaining):
            raise FrameSubmissionError("native_subset_annotation_count_mismatch")
        rows, rejected = [], {}
        temporary = round_dir.with_name(round_dir.name + ".partial")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        try:
            for media_id, annotation in zip(remaining, downloaded):
                value = annotation.to_json()
                errors = frame_completion_errors(value)
                if errors:
                    rejected[media_id] = errors
                    continue
                asset, native = source[media_id], mapping[media_id]
                row = {
                    "source_media_id": media_id,
                    "source_relative_path": asset.relative_path,
                    "source_sha256": asset.sha256,
                    "native_dataset_id": native.native_dataset_id,
                    "native_image_id": native.native_image_id,
                    "native_job_id": native_job_id,
                    "annotation": value,
                }
                with (temporary / f"{media_id}.json").open("w", encoding="utf-8") as file:
                    json.dump(row, file, ensure_ascii=False, separators=(",", ":"))
                rows.append({"source_media_id": media_id})
            with (temporary / "manifest.json").open("w", encoding="utf-8") as file:
                json.dump({
                    "schema": "nightjar-validated-frames-v1",
                    "event_id": job.event_id, "job_id": job.id,
                    "correction_round": round_number,
                    "native_job_id": native_job_id,
                    "media": rows, "incomplete": rejected,
                    "submitted_ids": remaining,
                    "captured_at": epoch(),
                }, file, ensure_ascii=False, separators=(",", ":"))
            round_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, round_dir)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    details = json.loads(manifest.read_text(encoding="utf-8"))
    if (details.get("schema") != "nightjar-validated-frames-v1"
            or details.get("job_id") != job.id
            or details.get("event_id") != job.event_id
            or details.get("correction_round") != round_number
            or details.get("native_job_id") != native_job_id):
        raise FrameSubmissionError("snapshot_manifest_identity_conflict")
    previously_accepted = accepted_ids(db, job)
    accepted = []
    for entry in details["media"]:
        media_id = entry["source_media_id"]
        if media_id not in job.media_ids:
            raise FrameSubmissionError("snapshot_source_media_mismatch")
        if media_id in previously_accepted:
            continue
        raw = (round_dir / f"{media_id}.json").read_bytes()
        snapshot = json.loads(raw)
        if (snapshot.get("source_media_id") != media_id
                or frame_completion_errors(snapshot.get("annotation"))):
            raise FrameSubmissionError("snapshot_validation_failed")
        snapshot["sha256"] = hashlib.sha256(raw).hexdigest()
        snapshot["selection_reason"] = "structurally_complete_submitted_frame"
        db.add(ReviewSample(
            event_id=job.event_id, job_id=job.id, team_id=job.team_id,
            media_id=media_id, correction_round=round_number,
            validated_complete=True, snapshot=snapshot,
        ))
        accepted.append(media_id)
    db.flush()
    pending = pending_ids(db, job)
    job.annotation_snapshot_round = round_number
    job.annotation_snapshot_frames = job.frame_count - len(pending)
    job.annotation_snapshot_annotated_frames = job.annotation_snapshot_frames
    if not pending:
        from .models import AuditEvent
        job.status = "on_review"
        job.submitted_at = job.submitted_at or epoch()
        if job.release_mode in {"shared_primary", "shared_repeat"}:
            packet = db.get(WorkPacket, job.id)
            if packet is None or packet.person_id != job.assignee_person_id:
                raise FrameSubmissionError("shared_packet_missing")
            packet.submitted_at = packet.submitted_at or epoch()
        job.review_status = "unreviewed"
        db.add(AuditEvent(
            event_id=job.event_id, action="native.job.frame_validation_complete",
            target_type="labeling_job", target_id=job.id,
            details={"accepted_total": job.frame_count, "round": job.correction_round},
        ))
    return {"accepted": accepted, "pending": pending}
