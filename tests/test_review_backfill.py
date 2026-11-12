"""Existing submitted JSON can be safely backfilled without native SDK access."""

import json
from pathlib import Path

from sqlalchemy import func, select
from test_dataset_workflows import act, ingest, teams

from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    LabelingJob,
    MediaAsset,
    MediaDataset,
    Person,
    ReviewSample,
)
from annotatiathon.review_backfill import backfill_frame_reviews

dataset_ctx = __import__("test_dataset_workflows").ctx


def test_backfill_all_submitted_frames_is_idempotent(dataset_ctx):
    http, maker, csrf = dataset_ctx
    dataset_id = ingest(http, csrf)
    teams(http, csrf)
    plan = act(http, csrf, "/labeling/plans", {"dataset_id": dataset_id})
    assert plan.status_code == 201, plan.text
    with maker.begin() as db:
        jobs = list(db.scalars(select(LabelingJob).where(
            LabelingJob.plan_id == plan.json()["plan_id"],
        )))
        job = next(row for row in jobs if len(row.media_ids) >= 2)
        job.assignee_person_id = db.scalar(select(Person.id).where(
            Person.team_id == job.team_id,
        ).limit(1))
        job.status = "completed"
        dataset = db.get(MediaDataset, dataset_id)
        destination = Path(dataset.root_path) / "annotations" / job.id / "round-0"
        destination.mkdir(parents=True)
        for media_id in job.media_ids:
            asset = db.get(MediaAsset, media_id)
            snapshot = {
                "source_media_id": media_id,
                "source_relative_path": asset.relative_path,
                "annotation": {"objects": [{"classTitle": "bird",
                                            "geometryType": "rectangle"}]},
            }
            (destination / (media_id + ".json")).write_text(
                json.dumps(snapshot), encoding="utf-8",
            )
        (destination / "manifest.json").write_text(json.dumps({
            "job_id": job.id,
            "correction_round": job.correction_round,
            "event_id": job.event_id,
        }), encoding="utf-8")
        wanted = len(job.media_ids)
        job_id = job.id
    first = backfill_frame_reviews(maker, event_id=DEFAULT_EVENT_ID)
    assert first["frames_queued"] == wanted
    assert first["jobs_updated"] == 1
    second = backfill_frame_reviews(maker, event_id=DEFAULT_EVENT_ID)
    assert second["frames_queued"] == 0
    with maker() as db:
        assert db.scalar(select(func.count(ReviewSample.id)).where(
            ReviewSample.job_id == job_id,
        )) == wanted
