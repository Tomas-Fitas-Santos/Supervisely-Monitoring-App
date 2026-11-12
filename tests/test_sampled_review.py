"""Exercise the sampled review routers before production route registration."""
import json

import test_dataset_workflows
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_dataset_workflows import act, ingest, teams

from annotatiathon.app import app
from annotatiathon.models import LabelingJob, MediaAsset, Person
from annotatiathon.review_queue_api import ingest_sample
from annotatiathon.review_queue_api import router as review_router
from annotatiathon.scoring_api import router as scoring_router

dataset_ctx = test_dataset_workflows.ctx


def test_sample_claim_scoring_and_retry(dataset_ctx, tmp_path):
    client, maker, csrf = dataset_ctx
    dataset_id = ingest(client, csrf)
    teams(client, csrf)
    planned = act(client, csrf, "/labeling/plans", {"dataset_id": dataset_id})
    assert planned.status_code == 201, planned.text
    with maker.begin() as db:
        jobs = db.scalars(select(LabelingJob).where(
            LabelingJob.plan_id == planned.json()["plan_id"],
        )).all()
        job = next(j for j in jobs if len(j.media_ids) >= 2)
        job.release_mode = "shared_primary"
        job.status = "on_review"
        job.assignee_person_id = db.scalar(select(Person.id).where(
            Person.team_id == job.team_id,
        ).limit(1))
        assert job.assignee_person_id is not None
        reviewed_job_id = job.id
        for asset_id in job.media_ids:
            asset = db.get(MediaAsset, asset_id)
            snapshot = {
                "source_media_id": asset.id,
                "source_relative_path": asset.relative_path,
                "annotation": {"objects": [
                    {"classTitle": "bird", "geometryType": "rectangle"},
                    {"classTitle": "beak", "geometryType": "point"},
                ]},
            }
            (tmp_path / f"{asset.id}.json").write_text(
                json.dumps(snapshot), encoding="utf-8",
            )
        frames = len(job.media_ids)
        ingest_sample(db, job, tmp_path)
        db.flush()
        ingest_sample(db, job, tmp_path)

    api = FastAPI()
    api.include_router(review_router)
    api.include_router(scoring_router)
    api.dependency_overrides.update(app.dependency_overrides)
    with TestClient(api, cookies=client.cookies) as http:
        assert http.get("/api/v1/review-queue/status").json()["pending"] == frames
        claimed = act(http, csrf, "/review-queue/claim-next")
        assert claimed.status_code == 200, claimed.text
        sample = claimed.json()["review"]
        assert sample["rubric_version"] == 0
        changed = act(http, csrf, "/scoring/rubric", {"box": 20}, method="PUT")
        assert changed.status_code == 200, changed.text
        review_id = sample["review_id"]
        payload = {"claim_token": sample["claim_token"], "action": "pause", "verdicts": [
            {"object_index": 0, "correct": True},
            {"object_index": 1, "correct": False, "reason": "wrong_class",
             "corrected_class": "crown"},
        ]}
        for _ in range(2):
            completed = act(http, csrf, f"/review-queue/{review_id}/complete", payload)
            assert completed.status_code == 200, completed.text
        top = http.get("/api/v1/rankings/teams").json()["items"][0]
        assert top["correct_objects"] == top["correct_boxes"] == 1
        assert top["weighted_score"] == 20
        assert top["reviewed_frames"] == 1
        assert top["errors"] == {"wrong_class": 1}
        # Changing the scoring policy during the event does not rewrite
        # decisions, but recalculates every team's weighted tie breaker.
        assert act(http, csrf, "/scoring/rubric", {
            "keypoint": 1, "box": 3, "wrong_class": 1,
        }, method="PUT").status_code == 200
        rescored = http.get("/api/v1/rankings/teams").json()
        assert rescored["rubric_version"] == 2
        assert rescored["items"][0]["weighted_score"] == 2
        state = http.get("/api/v1/review-queue/status").json()
        assert state["pending"] == frames - 1
        assert state["completed"] == 1
        with maker.begin() as db:
            job = db.get(LabelingJob, reviewed_job_id)
            job.release_mode = "shared_repeat"
            # Repeat submissions must not create duplicate review records.
            ingest_sample(db, job, tmp_path)
        assert http.get("/api/v1/review-queue/status").json()["pending"] == frames - 1


def test_publishing_runs_while_background_is_busy(monkeypatch):
    from contextlib import nullcontext
    from threading import Event

    from annotatiathon import native_worker

    background_started = Event()
    published = Event()

    class Finished(Exception):
        pass

    def cycle(lane):
        if lane == "background":
            background_started.set()
            assert published.wait(3)
            return True
        assert lane == "publish"
        assert background_started.wait(3)
        published.set()
        raise Finished

    monkeypatch.setattr(native_worker, "worker_cycle", cycle)
    monkeypatch.setattr(native_worker, "session_factory", lambda: nullcontext)
    monkeypatch.setattr(native_worker, "reset_pending", lambda _db: False)
    try:
        native_worker.run_worker(once=False, poll_seconds=3)
    except Finished:
        pass
    assert published.is_set()


def test_current_round_supersedes_old_review_only_after_new_review(dataset_ctx, tmp_path):
    """A re-submission must never double-score a previous reviewed round."""
    from annotatiathon.models import ReviewSample

    http, maker, csrf = dataset_ctx
    dataset_id = ingest(http, csrf)
    teams(http, csrf)
    planned = act(http, csrf, "/labeling/plans", {"dataset_id": dataset_id})
    assert planned.status_code == 201
    with maker.begin() as db:
        job = db.scalar(select(LabelingJob).where(
            LabelingJob.plan_id == planned.json()["plan_id"],
        ))
        job.assignee_person_id = db.scalar(select(Person.id).where(
            Person.team_id == job.team_id,
        ).limit(1))
        assert job.assignee_person_id
        asset_id = job.media_ids[0]
        original = ReviewSample(
            event_id=job.event_id, job_id=job.id, team_id=job.team_id,
            media_id=asset_id, correction_round=0,
            snapshot={"sha256": "0" * 64, "source_relative_path": "a.jpg",
                      "annotation": {"objects": []}, "selection_reason": "submitted_frame"},
            decision={"correct_keypoints": 3, "correct_boxes": 1,
                      "errors": {}, "missing_classes": [], "weighted_score": 4},
            completed_at=200,
        )
        db.add(original)
        first_job = job.id
    with maker() as db:
        from annotatiathon.auth import AuthContext
        from annotatiathon.models import DEFAULT_EVENT_ID, StaffAccount, StaffSession
        from annotatiathon.scoring_api import rankings
        staff = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "owner@example.org",
        ))
        ctx = AuthContext(staff, StaffSession(event_id=DEFAULT_EVENT_ID))
        previous = rankings(ctx, db)["items"]
        leader = next(row for row in previous if row["correct_objects"])
        assert leader["correct_objects"] == 4
    with maker.begin() as db:
        job = db.get(LabelingJob, first_job)
        job.correction_round = 1
    with maker() as db:
        current = rankings(ctx, db)["items"]
        assert all(row["correct_objects"] == 0 for row in current)
    with maker.begin() as db:
        job = db.get(LabelingJob, first_job)
        db.add(ReviewSample(
            event_id=job.event_id, job_id=job.id, team_id=job.team_id,
            media_id=asset_id, correction_round=1,
            snapshot={"sha256": "1" * 64, "source_relative_path": "a.jpg",
                      "annotation": {"objects": []}, "selection_reason": "submitted_frame"},
            decision={"correct_keypoints": 2, "correct_boxes": 0,
                      "errors": {}, "missing_classes": [], "weighted_score": 2},
            completed_at=300,
        ))
    with maker() as db:
        current = rankings(ctx, db)["items"]
        assert sum(row["correct_objects"] for row in current) == 2
