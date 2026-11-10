"""Local end-to-end acceptance: ingest, plan, review, closeout without demo mode."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    Event,
    LabelingJob,
    MediaAsset,
    StaffAccount,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner",
            roles=["organiser"], password_hash=password_hash("StrongLocalPassword!"),
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    monkeypatch.setattr(
        "annotatiathon.datasets_api.get_settings",
        lambda: type("Settings", (), {
            "local_data_dir": str(tmp_path),
            "max_dataset_bytes": 150_000_000_000,
            "max_dataset_assets": 250000,
        })(),
    )
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={
            "email": "owner@example.org", "password": "StrongLocalPassword!",
        })
        assert login.status_code == 200
        yield client, maker, login.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def act(client, csrf, path, body=None, method="POST"):
    return client.request(
        method, "/api/v1" + path, json=body, headers={"X-CSRF-Token": csrf},
    )


def ingest(client, csrf):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in (
            "Dataset/images/wings/a.jpg",
            "Dataset/images/beaks/b.jpg",
            "Dataset/videos/wings/Flight01/0001.jpg",
            "Dataset/videos/wings/Flight01/0002.jpg",
        ):
            zf.writestr(filename, b"small bird image bytes")
    source = output.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Local Media", "upload_kind": "zip",
    })
    assert created.status_code == 201, created.text
    dsid = created.json()["id"]
    midpoint = len(source)//2
    for offset, part in ((0, source[:midpoint]), (midpoint, source[midpoint:])):
        result = client.put(
            "/api/v1/datasets/uploads/"+dsid+"/chunk",
            params={"relative_path": "source.zip", "offset": offset, "total": len(source)},
            data=part, headers={"X-CSRF-Token": csrf},
        )
        assert result.status_code == 200, result.text
    done = act(client, csrf, "/datasets/"+dsid+"/finalize")
    assert done.status_code == 200, done.text
    assert done.json()["image_count"] == 2
    assert done.json()["frame_count"] == 2
    assert done.json()["video_count"] == 1
    assert act(client, csrf, "/datasets/"+dsid+"/finalize").status_code == 200
    summary = client.get("/api/v1/datasets/"+dsid+"/summary").json()
    assert summary["total_assets"] == 4
    assert len(summary["morphologies"]) == 3
    return dsid


def teams(client, csrf):
    for n in range(3):
        for i in range(2):
            result = act(client, csrf, "/people", {
                "name": f"Participant {n}-{i}",
                "email": f"p{n}-{i}@example.org",
                "kind": "participant",
                "team_name": f"Team {n}",
            })
            assert result.status_code == 201, result.text


def test_local_upload_planning_and_atomic_video_groups(ctx):
    client, maker, csrf = ctx
    dsid = ingest(client, csrf)
    teams(client, csrf)
    plan = act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "jobs_per_team": 2, "redundancy": 2,
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["assets"] == 4
    assert plan.json()["assignment_copies"] == 8
    approve = act(client, csrf, "/labeling/plans/"+plan.json()["plan_id"]+"/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    jobs = client.get("/api/v1/labeling/jobs").json()["items"]
    assert jobs
    with maker() as db:
        assigned: dict[str, set[str]] = {}
        for job in db.scalars(select(LabelingJob)):
            for mid in job.media_ids:
                assigned.setdefault(mid, set()).add(job.team_id)
        assert all(len(tids) == 2 for tids in assigned.values())
        assets = list(db.scalars(select(MediaAsset)))
        frames = {a.id for a in assets if a.kind == "video_frame"}
        for team in {job.team_id for job in db.scalars(select(LabelingJob))}:
            groups = [
                {m for m in job.media_ids if m in frames}
                for job in db.scalars(select(LabelingJob).where(
                    LabelingJob.team_id == team
                ))
            ]
            assert all(not item or item == frames for item in groups)
    assert client.get("/api/v1/datasets/"+dsid+"/assets?page=1&size=2").json()["total"] == 4
    image_id = client.get("/api/v1/datasets/"+dsid+"/assets").json()["items"][0]["id"]
    preview = client.get("/api/v1/review/datasets/"+dsid+"/assets/"+image_id+"/image")
    assert preview.status_code == 200
    assert preview.content == b"small bird image bytes"


def test_review_and_csv_and_closeout_require_accepted_jobs(ctx):
    client, maker, csrf = ctx
    dsid = ingest(client, csrf)
    teams(client, csrf)
    result = act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "jobs_per_team": 1, "redundancy": 1,
    })
    assert result.status_code == 201, result.text
    plan_id = result.json()["plan_id"]
    assert act(client, csrf, "/labeling/plans/"+plan_id+"/approve").status_code == 200
    with maker.begin() as db:
        first = db.scalar(select(LabelingJob).order_by(LabelingJob.id))
        first.status = "on_review"
        job_id = first.id
        media_id = first.media_ids[0]
    frame = act(client, csrf, "/review/jobs/"+job_id+"/frames", {
        "media_id": media_id, "verdict": "good",
        "correct_keypoints": 8, "missing_keypoints": 2, "incorrect_keypoints": 1,
        "visible_keypoints": 7, "occluded_keypoints": 2,
        "temporal_consistency": True,
    }, "PUT")
    assert frame.status_code == 200, frame.text
    assert frame.json()["keypoint_score"] == 6
    reviewed = client.get("/api/v1/review/jobs/"+job_id)
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_frames"] == 1
    assert act(client, csrf, "/review/jobs/"+job_id+"/decision", {
        "decision": "accept",
    }).status_code == 200
    report = client.get("/api/v1/closeout/report").json()
    assert report["reviews"]["keypoint_score"] == 6
    assert report["labeling"]["jobs_needing_action"] > 0
    assert act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    }).status_code == 409
    with maker.begin() as db:
        for job in db.scalars(select(LabelingJob).where(
            LabelingJob.status == "queued"
        )):
            job.status = "completed"
            job.review_status = "accepted"
    closed = act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    })
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "archived"
    assert client.get("/api/v1/closeout/report").json()["event_status"] == "archived"
    csv_response = client.get("/api/v1/closeout/exports/media")
    assert csv_response.status_code == 200
    assert csv_response.text.count("wings") >= 2
    assert act(client, csrf, "/datasets/uploads", {
        "name": "Blocked", "upload_kind": "zip",
    }).status_code == 409


def test_dataset_upload_rejects_unsafe_path(ctx):
    client, _maker, csrf = ctx
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Safe Test", "upload_kind": "folder",
    })
    assert created.status_code == 201
    dsid = created.json()["id"]
    invalid = client.put("/api/v1/datasets/uploads/"+dsid+"/chunk",
        params={"relative_path": "../outside.jpg", "offset": 0, "total": 1},
        data=b"x", headers={"X-CSRF-Token": csrf})
    assert invalid.status_code == 422
