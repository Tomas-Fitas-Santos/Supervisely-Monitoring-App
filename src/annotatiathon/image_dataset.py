"""Explicit correction of unused photo folders imported as supplied video frames."""
from fastapi import HTTPException
from sqlalchemy import delete, select

from .models import (
    AuditEvent, DatasetAppendBatch, DatasetMovementSegment, DatasetPreparedQueue,
    LabelingJob, LabelingPlan, MediaVideo, NativeOperation,
)
from .native_media_pool import mirror_status


def reclassify_supplied_images(db, dataset, assets, staff_id):
    """Called inside plan creation's event/dataset lock and transaction.

    Preserve file paths, hashes, asset IDs and native mappings. Never reinterpret
    footage extracted from a video or backed by a supplied video manifest.
    """
    if dataset.upload_kind not in {"folder", "zip"}:
        raise HTTPException(409, "This correction supports uploaded photo folders or ZIPs only.")
    if db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.dataset_id == dataset.id,
    ).limit(1)) or db.scalar(select(LabelingJob.id).where(
        LabelingJob.dataset_id == dataset.id,
    ).limit(1)):
        raise HTTPException(409, "Only a dataset with no existing plans or assignments can be reclassified.")
    if db.scalar(select(DatasetAppendBatch.id).where(
        DatasetAppendBatch.dataset_id == dataset.id,
        DatasetAppendBatch.status.in_(["uploading", "indexing", "ready_to_commit"]),
    ).limit(1)) or db.scalar(select(NativeOperation.id).where(
        NativeOperation.target_id == dataset.id,
        NativeOperation.status.in_(["pending", "running"]),
    ).limit(1)):
        raise HTTPException(409, "Wait for dataset indexing, preparation and uploads to finish first.")
    if db.scalar(select(DatasetMovementSegment.id).where(
        DatasetMovementSegment.dataset_id == dataset.id,
    ).limit(1)):
        raise HTTPException(409, "Dataset has video movement annotations and cannot be reclassified.")
    videos = list(db.scalars(select(MediaVideo).where(MediaVideo.dataset_id == dataset.id)))
    if not videos or any(
        v.extraction_policy != "supplied_unknown" or v.source_sha256
        or v.source_relative_path or v.time_base or v.frame_rate for v in videos
    ):
        raise HTTPException(409, "Only supplied photo folders without original-video or manifest provenance can be reclassified.")
    video_ids = {v.id for v in videos}
    if not assets or any(
        a.kind not in {"image", "video_frame"}
        or (a.kind == "video_frame" and a.video_id not in video_ids)
        or a.presentation_timestamp is not None for a in assets
    ):
        raise HTTPException(409, "Dataset contains incompatible source identities.")
    if not mirror_status(db, dataset)["ready_for_automatic_jobs"]:
        raise HTTPException(409, "Complete the Supervisely upload before correcting the photo classification.")

    previous = {"images": dataset.image_count, "frames": dataset.frame_count,
                "videos": dataset.video_count, "inventory_revision": dataset.inventory_revision}
    for asset in assets:
        asset.kind = "image"
        asset.video_id = asset.video_group = asset.frame_index = None
    # Keep the previous inferred folder identities in the audit record.
    db.add(AuditEvent(
        event_id=dataset.event_id, staff_id=staff_id,
        action="dataset.reclassify_standalone_images", target_type="media_dataset",
        target_id=dataset.id,
        details={"previous": previous, "images": len(assets),
                 "previous_folders": [{"id": v.id, "group": v.video_group,
                                       "identity_notes": v.identity_notes} for v in videos],
                 "native_mutations": False},
    ))
    db.flush()  # Detach asset foreign keys before removing inferred video rows.
    db.execute(delete(MediaVideo).where(MediaVideo.dataset_id == dataset.id))
    dataset.image_count, dataset.frame_count, dataset.video_count = len(assets), 0, 0
    dataset.inventory_revision += 1
    # Only classification changed; every existing native image mapping remains valid.
    dataset.mirrored_revision = dataset.inventory_revision
    prepared = db.get(DatasetPreparedQueue, dataset.id)
    if prepared:
        prepared.state = "stale"
    db.flush()
