import os
from datetime import date, datetime
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy import event as sql_event
from sqlalchemy.orm import Session
from test_native_dispatch import configured as configured  # noqa: PLC0414 - shared fixture
from test_validation_task_queue import setup_queue

from annotatiathon import models as m
from annotatiathon.annotation_statistics import LISBON, annotation_statistics, event_window

START = int(datetime(2026, 11, 13, 19, tzinfo=LISBON).timestamp())


@pytest.fixture
def inventory():
    engine = setup_queue()
    with Session(engine) as db, db.begin():
        event = db.get(m.Event, "event")
        event.start_at, event.end_at = START, START + 43200
        job = db.get(m.LabelingJob, "job")
        job.media_ids, job.frame_count = ["image1", "image2", "image3"], 3
        for mid, kind in (("image1", "video_frame"), ("image2", "image"), ("image3", "video_frame")):
            db.add(m.MediaAsset(id=mid, event_id="event", dataset_id="dataset", relative_path=mid+".jpg",
                               kind=kind, morphology="unknown", size_bytes=1, sha256="0"*64))
        db.flush()
        for mid, round_number, timestamp, reviewed in (
            ("image1", 0, START+1800, True), ("image1", 1, START+4000, False),
            ("image2", 0, START+5400, False),
        ):
            db.add(m.ReviewSample(event_id="event", job_id="job", team_id="team", media_id=mid,
                correction_round=round_number, validated_complete=True,
                snapshot={"validated_at": timestamp, "annotation": {"objects": []}},
                completed_at=START+2000 if reviewed else None))
        # Merely downloaded/reviewed legacy content is not a structurally complete annotation.
        db.add(m.ReviewSample(event_id="event", job_id="job", team_id="team", media_id="image3",
            correction_round=0, validated_complete=False, snapshot={}, completed_at=START+5000))
    yield engine
    engine.dispose()


def test_unique_coverage_recent_rate_corrections_and_capped_forecast(inventory):
    with Session(inventory) as db:
        result = annotation_statistics(db, db.get(m.Event, "event"), now=START+7200)
        assert result["totals"] == {"total": 3, "complete": 2, "reviewed": 1, "remaining": 1,
                                    "awaiting_review": 1, "percent": 66.7, "undated_completions": 0}
        assert result["kinds"]["video_frame"] == {"total": 2, "complete": 1, "reviewed": 1}
        assert result["kinds"]["image"] == {"total": 1, "complete": 1, "reviewed": 0}
        assert result["forecast"]["recent_per_hour"] == 1
        assert result["forecast"]["average_per_hour"] == 1
        assert result["forecast"]["projected_complete"] == 3
        assert [row["complete"] for row in result["hourly"][:3]] == [1, 1, 0]
        assert len(result["hourly"]) == 12
        assert result["datasets"][0]["complete"] == 2


def test_repeat_jobs_do_not_inflate_coverage_or_rates(inventory):
    with Session(inventory) as db:
        db.add(m.LabelingJob(id="repeat", event_id="event", dataset_id="dataset", plan_id="plan",
            team_id="team", sequence=2, release_mode="shared_repeat", status="on_review",
            assignee_person_id="person", media_ids=["image1"], frame_count=1, video_count=1,
            morphology_counts={}))
        db.flush()
        db.add(m.ReviewSample(event_id="event", job_id="repeat", team_id="team", media_id="image1",
            correction_round=0, validated_complete=True, snapshot={"validated_at": START+6000}))
        db.commit()
        result = annotation_statistics(db, db.get(m.Event, "event"), now=START+7200)
        assert result["totals"]["complete"] == 2
        assert result["forecast"]["recent_completions"] == 1
        assert result["frame_assignments"] == 4


def test_unknown_historical_times_not_invented_and_scope_is_event_local(inventory):
    with Session(inventory) as db:
        db.add(m.ReviewSample(event_id="event", job_id="job", team_id="team", media_id="image1",
            correction_round=2, validated_complete=True, snapshot={}))
        db.add(m.Event(id="other", name="Other event"))
        db.flush()
        db.add(m.MediaDataset(id="other-ds", event_id="other", name="Private", root_path="/unused",
                              status="ready", upload_kind="mounted", created_by="staff"))
        db.flush()
        db.add(m.MediaAsset(id="other-asset", event_id="other", dataset_id="other-ds", relative_path="a.jpg",
                           kind="image", morphology="unknown", size_bytes=1, sha256="0"*64))
        db.commit()
        result = annotation_statistics(db, db.get(m.Event, "event"), now=START+7200)
        assert result["totals"]["total"] == 3
        assert result["totals"]["complete"] == 2
        assert result["totals"]["undated_completions"] == 1
        assert result["forecast"]["completed_in_window"] == 1


@pytest.mark.parametrize(("offset", "status"), [(-1, "before_start"), (300, "warming_up"),
                                                (1000, "no_observations"), (43200, "ended")])
def test_projection_requires_observations_and_running_event_window(inventory, offset, status):
    with Session(inventory) as db:
        result = annotation_statistics(db, db.get(m.Event, "event"), now=START+offset)
        assert result["forecast"]["status"] == status
        assert result["forecast"]["projected_complete"] is None
        assert result["schedule"]["elapsed_seconds"] >= 0


def test_stalled_rate_does_not_project_previous_hours_forever(inventory):
    with Session(inventory) as db:
        result = annotation_statistics(db, db.get(m.Event, "event"), now=START+14400)
        assert result["forecast"]["recent_per_hour"] == 0
        assert result["forecast"]["projected_complete"] == 2


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 11, 13), 12), (date(2026, 3, 28), 11), (date(2026, 10, 24), 13)])
def test_lisbon_overnight_window_and_clock_changes(day, hours):
    event = SimpleNamespace(start_at=None, end_at=None)
    start, end, source = event_window(event, START, day)
    assert source == "preview" and (end-start)/3600 == hours
    assert datetime.fromtimestamp(start, LISBON).hour == 19
    assert datetime.fromtimestamp(end, LISBON).hour == 7
    assert datetime.fromtimestamp(end, LISBON).date() > day
    assert event.start_at is None  # previews never edit the real event


def test_early_morning_preview_uses_previous_day_and_saved_schedule_wins():
    event = SimpleNamespace(start_at=None, end_at=None)
    assert event_window(event, START+8*3600)[:2] == (START, START+43200)
    event.start_at, event.end_at = START, START+43200
    assert event_window(event, START, date(2027, 1, 1)) == (START, START+43200, "configured")


def test_api_is_read_only_and_permissions_match_job_progress(configured):
    http, maker, _csrf, _plan, _sdk = configured
    response = http.get("/api/v1/labeling/annotation-statistics?event_date=2026-11-13")
    assert response.status_code == 200, response.text
    assert response.json()["totals"]["total"] == 3
    assert response.json()["forecast"]["projected_complete"] is None
    with maker.begin() as db:
        assert db.get(m.Event, m.DEFAULT_EVENT_ID).start_at is None
        from sqlalchemy import select
        staff = db.scalar(select(m.StaffAccount))
        staff.roles = ["reception"]
    assert http.get("/api/v1/labeling/annotation-statistics").status_code == 403


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_100000_frames_aggregate_in_database_without_loading_annotations():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"],
                           connect_args={"options": "-c statement_timeout=15000"})
    suffix = m.identifier()[:8]
    eid, sid, dsid, tid, pid, jid = [f"stats-{name}-{suffix}" for name in ("event", "staff", "ds", "team", "plan", "job")]
    try:
        with Session(engine) as db, db.begin():
            db.add(m.Event(id=eid, name=eid, start_at=START, end_at=START+43200))
            db.add(m.StaffAccount(id=sid, name="Test", email=sid+"@example.test", password_hash="disabled", roles=["organiser"]))
            db.flush()
            db.add(m.MediaDataset(id=dsid, event_id=eid, name=dsid, root_path="/not-used",
                upload_kind="mounted", status="ready", created_by=sid))
            db.add(m.CompetitionTeam(id=tid, event_id=eid, name=tid))
            db.flush()
            db.add(m.LabelingPlan(id=pid, event_id=eid, dataset_id=dsid, created_by=sid,
                                 jobs_per_team=0, redundancy=1, status="running"))
            db.flush()
            db.add(m.LabelingJob(id=jid, event_id=eid, dataset_id=dsid, team_id=tid, plan_id=pid,
                sequence=1, media_ids=[], frame_count=100000, video_count=1,
                morphology_counts={}, status="on_review"))
            db.flush()
            for offset in range(0, 100000, 5000):
                db.execute(insert(m.MediaAsset), [{"id": f"stats-{suffix}-{i}", "event_id": eid,
                    "dataset_id": dsid, "relative_path": f"{i}.jpg", "kind": "video_frame",
                    "morphology": "unknown", "size_bytes": 1, "sha256": "0"*64}
                    for i in range(offset, offset+5000)])
                if offset < 50000:
                    db.execute(insert(m.ReviewSample), [{"event_id": eid, "job_id": jid,
                        "team_id": tid, "media_id": f"stats-{suffix}-{i}", "correction_round": 0,
                        "validated_complete": True, "snapshot": {"validated_at": START+5400}}
                        for i in range(offset, offset+5000)])
        queries = []

        def count_queries(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        sql_event.listen(engine, "before_cursor_execute", count_queries)
        with Session(engine) as db:
            event = db.get(m.Event, eid)
            started = perf_counter()
            result = annotation_statistics(db, event, now=START+7200)
            elapsed = perf_counter()-started
        assert result["totals"]["total"] == 100000
        assert result["totals"]["complete"] == 50000
        assert result["forecast"]["recent_per_hour"] == 50000
        assert result["forecast"]["projected_complete"] == 100000
        assert len(queries) <= 7
        print(f"100,000-source statistics: {elapsed:.3f}s, {len(queries)} aggregate/read queries")
    finally:
        engine.dispose()
