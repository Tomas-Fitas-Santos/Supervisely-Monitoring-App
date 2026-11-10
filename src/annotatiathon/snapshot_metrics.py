"""Durable snapshot counters, with an explicit idempotent historical backfill."""
import json
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import LabelingJob, MediaDataset


def cache_snapshot_counts(db: Session, job: LabelingJob, manifest: Path) -> None:
    details = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(details, dict):
        raise ValueError("Snapshot manifest must be a JSON object.")
    if details.get("job_id", job.id) != job.id or details.get("event_id", job.event_id) != job.event_id:
        raise ValueError("Snapshot identity mismatch")
    media = details.get("media")
    if not isinstance(media, list):
        raise ValueError("Snapshot frame inventory is missing")
    identifiers = [entry.get("source_media_id") for entry in media if isinstance(entry, dict)]
    if (len(identifiers) != len(media) or not all(isinstance(value, str) for value in identifiers)
            or len(set(identifiers)) != len(identifiers) or set(identifiers) != set(job.media_ids)
            or len(identifiers) != job.frame_count
            or details.get("correction_round", job.correction_round) != job.correction_round):
        raise ValueError("Snapshot frame inventory is incomplete")
    count = details.get("annotated_frames")
    if type(count) is not int or not 0 <= count <= job.frame_count:
        count = None
    job.annotation_snapshot_round = job.correction_round
    job.annotation_snapshot_frames = job.frame_count
    job.annotation_snapshot_annotated_frames = count
    db.commit()


def backfill_snapshot_counts(db: Session, event_id: str | None = None, batch_size: int = 250) -> dict:
    last_id = ""
    counts = {"cached": 0, "missing": 0, "unreadable": 0}
    while True:
        query = select(LabelingJob, MediaDataset.root_path).join(
            MediaDataset, MediaDataset.id == LabelingJob.dataset_id,
        ).where(LabelingJob.id > last_id, or_(LabelingJob.annotation_snapshot_round.is_(None),
                                            LabelingJob.annotation_snapshot_round != LabelingJob.correction_round),
                LabelingJob.submitted_at.is_not(None))
        if event_id is not None:
            query = query.where(LabelingJob.event_id == event_id)
        rows = list(db.execute(query.order_by(LabelingJob.id).limit(batch_size)))
        if not rows:
            return counts
        for job, root in rows:
            last_id = job.id
            path = Path(root) / "annotations" / job.id / f"round-{job.correction_round}" / "manifest.json"
            if not path.is_file():
                counts["missing"] += 1
                continue
            try:
                cache_snapshot_counts(db, job, path)
            except (ValueError, OSError):
                db.rollback()
                counts["unreadable"] += 1
            else:
                counts["cached"] += 1
