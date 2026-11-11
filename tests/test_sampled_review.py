"""Exercise the sampled review routers before production route registration."""
import json

import test_dataset_workflows
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_dataset_workflows import act, ingest, teams

from annotatiathon.app import app
from annotatiathon.models import LabelingJob, MediaAsset
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
        job = db.scalar(select(LabelingJob).where(LabelingJob.plan_id == planned.json()["plan_id"]))
        job.release_mode = "shared_primary"
        asset = db.get(MediaAsset, job.media_ids[0])
        snapshot = {
            "source_media_id": asset.id, "source_relative_path": asset.relative_path,
            "annotation": {"objects": [
                {"classTitle": "bird", "geometryType": "rectangle"},
                {"classTitle": "beak", "geometryType": "point"},
            ]},
        }
        (tmp_path / f"{asset.id}.json").write_text(json.dumps(snapshot), encoding="utf-8")
        ingest_sample(db, job, tmp_path)
        db.flush()
        ingest_sample(db, job, tmp_path)

    api = FastAPI()
    api.include_router(review_router)
    api.include_router(scoring_router)
    api.dependency_overrides.update(app.dependency_overrides)
    with TestClient(api, cookies=client.cookies) as http:
        assert http.get("/api/v1/review-queue/status").json()["pending"] == 1
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
        assert top["correct_objects"] == top["correct_boxes"] == top["weighted_score"] == 1
        assert top["reviewed_frames"] == 1
        assert top["errors"] == {"wrong_class": 1}
        assert act(http, csrf, "/review-queue/claim-next").json() == {"review": None}


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
