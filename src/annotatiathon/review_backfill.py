"""Recover existing submitted per-frame JSON without altering completed reviews.

The CLI scans immutable local snapshot manifests, never contacts Supervisely,
never downloads media, and records one frame-review candidate per submitted
(job, correction round, media ID). Run with the native worker stopped to
avoid concurrent ingestion while deploying the full-frame review migration.
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import LabelingJob, MediaDataset, ReviewSample
from .review_queue_api import ingest_sample


def backfill_frame_reviews(factory: sessionmaker, *, event_id: str | None = None) -> dict:
    result = {"jobs_scanned": 0, "jobs_updated": 0, "frames_queued": 0,
              "missing_snapshots": 0, "failed_jobs": 0}
    cursor = ""
    while True:
        with factory() as db:
            scope = select(LabelingJob.id).where(
                LabelingJob.id > cursor,
                LabelingJob.assignee_person_id.is_not(None),
                LabelingJob.status.in_(("on_review", "completed")),
            )
            if event_id is not None:
                scope = scope.where(LabelingJob.event_id == event_id)
            batch = list(db.scalars(scope.order_by(LabelingJob.id).limit(100)))
        if not batch:
            return result
        for job_id in batch:
            cursor = job_id
            with factory() as db:
                try:
                    _backfill_job(db, job_id, result)
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    db.rollback()
                    result["failed_jobs"] += 1


def _backfill_job(db: Session, job_id: str, result: dict) -> None:
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.id == job_id,
    ).with_for_update())
    if job is None:
        return
    result["jobs_scanned"] += 1
    dataset = db.get(MediaDataset, job.dataset_id)
    if dataset is None:
        result["failed_jobs"] += 1
        return
    root = Path(dataset.root_path) / "annotations" / job.id / (
        "round-" + str(job.correction_round)
    )
    manifest = root / "manifest.json"
    if not manifest.is_file():
        result["missing_snapshots"] += 1
        return
    header = json.loads(manifest.read_text(encoding="utf-8"))
    if (header.get("job_id") != job.id
            or header.get("correction_round") != job.correction_round
            or header.get("event_id") != job.event_id):
        raise ValueError("Snapshot manifest identity does not match the database job")
    before = db.scalar(select(func.count(ReviewSample.id)).where(
        ReviewSample.job_id == job.id,
        ReviewSample.correction_round == job.correction_round,
    )) or 0
    ingest_sample(db, job, root)
    db.flush()
    after = db.scalar(select(func.count(ReviewSample.id)).where(
        ReviewSample.job_id == job.id,
        ReviewSample.correction_round == job.correction_round,
    )) or 0
    db.commit()
    new_frames = after - before
    if new_frames:
        result["jobs_updated"] += 1
        result["frames_queued"] += new_frames
