"""Bounded query counts, stable pages and event/monitor-scoped media reads."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon import models as m
from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.security import token_digest


@pytest.fixture
def populated(tmp_path):
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(m.Event(id=m.DEFAULT_EVENT_ID, name="Capacity test"))
        for role in ("organiser", "monitor"):
            db.add(m.StaffAccount(id=role, email=f"{role}@example.test", name=role,
                                  roles=[role], password_hash="unused"))
        db.flush()
        for role in ("organiser", "monitor"):
            db.add(m.StaffSession(id=token_digest(role), staff_id=role,
                                  event_id=m.DEFAULT_EVENT_ID, csrf_token="csrf",
                                  expires_at=m.epoch() + 3600))
        db.add(m.Person(id="monitor-person", event_id=m.DEFAULT_EVENT_ID, name="Monitor",
                        email="monitor@example.test", kind="monitor"))
        for team in ("aaa", "zzz"):
            db.add(m.CompetitionTeam(id=team, event_id=m.DEFAULT_EVENT_ID, name=team))
        db.add(m.MediaDataset(id="dataset", event_id=m.DEFAULT_EVENT_ID, name="Frames",
                              upload_kind="folder", status="ready", root_path=str(tmp_path),
                              created_by="organiser"))
        db.flush()
        db.add(m.MonitorAssignment(event_id=m.DEFAULT_EVENT_ID, team_id="zzz",
                                   monitor_person_id="monitor-person", assigned_by="organiser"))
        db.add(m.LabelingPlan(id="plan", event_id=m.DEFAULT_EVENT_ID, dataset_id="dataset",
                              jobs_per_team=2000, redundancy=1, created_by="organiser"))
        for index in range(260):
            db.add(m.Person(id=f"person-{index}", event_id=m.DEFAULT_EVENT_ID,
                            name=f"Person {index}", email=f"person{index}@example.test",
                            kind="participant", team_id="aaa"))
        for asset_id in ("foreign", "owned"):
            path = tmp_path / "media" / f"{asset_id}.jpg"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"source image")
            db.add(m.MediaAsset(id=asset_id, event_id=m.DEFAULT_EVENT_ID, dataset_id="dataset",
                                relative_path=path.name, morphology="bird", kind="image",
                                size_bytes=12, sha256="a" * 64))
        db.flush()
        jobs = [{"id": f"foreign-{index}", "team_id": "aaa", "sequence": index + 1,
                 "assignee_person_id": f"person-{index % 260}", "media_ids": ["foreign"]}
                for index in range(2100)]
        jobs.extend({"id": f"own-{index}", "team_id": "zzz", "sequence": index + 1,
                     "assignee_person_id": f"person-{index}", "media_ids": ["owned"]}
                    for index in range(5))
        db.execute(insert(m.LabelingJob), [{**job, "event_id": m.DEFAULT_EVENT_ID,
            "plan_id": "plan", "dataset_id": "dataset", "frame_count": 1,
            "video_count": 0, "morphology_counts": {"bird": 1}} for job in jobs])
        db.execute(insert(m.NativeJobBinding), [{
            "event_id": m.DEFAULT_EVENT_ID, "job_id": job["id"],
            "person_id": job["assignee_person_id"], "native_job_id": index + 1,
        } for index, job in enumerate(jobs)])

    def dependency():
        with maker() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        client.cookies.set("annotatiathon_session", "organiser")
        yield client, maker, engine
    app.dependency_overrides.clear()
    engine.dispose()


@contextmanager
def sql_count(engine):
    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_large_page_has_constant_query_budget(populated):
    client, _, engine = populated
    with sql_count(engine) as statements:
        response = client.get("/api/v1/labeling/jobs?limit=2000")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2000
    assert len(statements) <= 10
    assert response.json()["items"][0]["assignee_name"] == "Person 0"
    assert response.json()["items"][0]["native_submissions"][0]["person_name"] == "Person 0"


def test_monitors_can_read_shared_event_jobs_with_global_limit(populated):
    client, _, engine = populated
    client.cookies.set("annotatiathon_session", "monitor")
    for path in ("/api/v1/review/assigned-jobs", "/api/v1/labeling/jobs"):
        with sql_count(engine) as statements:
            response = client.get(path + "?limit=250")
        assert response.status_code == 200
        assert [job["id"] for job in response.json()["items"]] == [
            f"foreign-{i}" for i in range(250)
        ]
        assert response.json()["next_cursor"] is not None
        assert len(statements) <= 10


def test_cursor_pages_are_stable_across_shared_event_teams(populated):
    client, _, _ = populated
    client.cookies.set("annotatiathon_session", "monitor")
    first = client.get("/api/v1/review/assigned-jobs?limit=2").json()
    assert len(first["items"]) == 2
    second = client.get("/api/v1/review/assigned-jobs", params={
        "limit": 2, "cursor": first["next_cursor"]}).json()
    third = client.get("/api/v1/review/assigned-jobs", params={
        "limit": 2, "cursor": second["next_cursor"]}).json()
    assert [row["id"] for page in (first, second, third) for row in page["items"]] == [
        f"foreign-{i}" for i in range(6)
    ]
    assert third["next_cursor"] is not None
    assert client.get("/api/v1/labeling/jobs?cursor=invalid").status_code == 422


def test_media_membership_is_exact_across_all_teams_in_selected_event(populated):
    client, maker, engine = populated
    client.cookies.set("annotatiathon_session", "monitor")
    with sql_count(engine) as statements:
        response = client.get("/api/v1/review/datasets/dataset/assets/owned/image")
    assert response.status_code == 200
    assert len(statements) <= 10
    assert client.get("/api/v1/review/datasets/dataset/assets/foreign/image").status_code == 200
    with maker.begin() as db:
        for index in range(5):
            db.get(m.LabelingJob, f"own-{index}").media_ids = ["foreign-suffix"]
    assert client.get("/api/v1/review/datasets/dataset/assets/owned/image").status_code == 403
    assert client.get("/api/v1/review/datasets/dataset/assets/foreign/image").status_code == 200


def test_team_metrics_aggregate_without_snapshot_filesystem_reads(populated, monkeypatch):
    from pathlib import Path
    client, maker, engine = populated
    with maker.begin() as db:
        job = db.get(m.LabelingJob, "own-0")
        job.started_at, job.submitted_at = 100, 160
        job.status, job.review_status = "completed", "accepted"
        job.annotation_snapshot_round = 0
        job.annotation_snapshot_frames = 1
        job.annotation_snapshot_annotated_frames = 1
        db.add(m.JobReview(event_id=m.DEFAULT_EVENT_ID, job_id=job.id, media_id="owned",
                           verdict="good", correct_keypoints=3, missing_keypoints=1,
                           incorrect_keypoints=1, reviewer_id="organiser"))
    def filesystem_scan(path):
        raise AssertionError("Dashboard requests must use durable snapshot counts")
    monkeypatch.setattr(Path, "is_file", filesystem_scan)
    with sql_count(engine) as statements:
        response = client.get("/api/v1/review/team-metrics")
    assert response.status_code == 200
    team = next(row for row in response.json()["teams"] if row["team_id"] == "zzz")
    assert team["frames_synced"] == 1
    assert team["frames_annotated"] == 1
    assert team["frames_reviewed"] == 1
    assert team["keypoint_accuracy_pct"] == 60
    assert team["average_seconds_per_frame"] == 60
    assert len(statements) <= 10


def test_incomplete_snapshot_manifest_is_not_cached_as_complete(populated, tmp_path):
    import json

    from annotatiathon.snapshot_metrics import cache_snapshot_counts
    _, maker, _ = populated
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"job_id": "own-0", "media": []}))
    with maker() as db, pytest.raises(ValueError):
        cache_snapshot_counts(db, db.get(m.LabelingJob, "own-0"), manifest)


def test_snapshot_count_backfill_is_idempotent_and_handles_corrections(populated, tmp_path):
    import json

    from annotatiathon.snapshot_metrics import backfill_snapshot_counts
    _, maker, _ = populated
    with maker.begin() as db:
        db.get(m.LabelingJob, "own-0").submitted_at = 100
    for correction_round, count in ((0, 0), (1, 1)):
        path = tmp_path / "annotations" / "own-0" / f"round-{correction_round}" / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"job_id": "own-0", "media": [{"source_media_id": "owned"}],
                                    "correction_round": correction_round, "annotated_frames": count}))
        with maker.begin() as db:
            db.get(m.LabelingJob, "own-0").correction_round = correction_round
        with maker() as db:
            assert backfill_snapshot_counts(db)["cached"] == 1
            assert backfill_snapshot_counts(db)["cached"] == 0
            job = db.get(m.LabelingJob, "own-0")
            assert job.annotation_snapshot_round == correction_round
            assert job.annotation_snapshot_annotated_frames == count
