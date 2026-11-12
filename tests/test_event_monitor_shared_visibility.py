"""Monitors see all event jobs after monitor-to-team assignments are retired."""

import test_dataset_workflows
from sqlalchemy import select
from test_dataset_workflows import act, ingest, teams

from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    CompetitionTeam,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MonitorAssignment,
    StaffAccount,
)
from annotatiathon.security import password_hash

dataset_ctx = test_dataset_workflows.ctx


def test_monitor_can_read_all_event_jobs_and_source_frames_without_assignments(dataset_ctx):
    http, maker, csrf = dataset_ctx
    dataset_id = ingest(http, csrf)
    teams(http, csrf)
    planned = act(http, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "jobs_per_team": 4, "redundancy": 1,
    })
    assert planned.status_code == 201, planned.text
    with maker.begin() as db:
        db.add(StaffAccount(
            name="Unassigned Event Monitor", email="monitor-all@example.org",
            password_hash=password_hash("EventMonitorPassword123!"),
            roles=["monitor"],
        ))
        jobs = list(db.scalars(select(LabelingJob).where(
            LabelingJob.plan_id == planned.json()["plan_id"],
        )))
        assert len({job.team_id for job in jobs}) >= 2
        assert db.scalar(select(MonitorAssignment.id).limit(1)) is None
        job_id = jobs[-1].id
    login = http.post("/api/v1/auth/login", json={
        "email": "monitor-all@example.org", "password": "EventMonitorPassword123!",
    })
    assert login.status_code == 200, login.text
    all_jobs = http.get("/api/v1/labeling/jobs?limit=2000")
    assert all_jobs.status_code == 200, all_jobs.text
    returned_ids = {job["id"] for job in all_jobs.json()["items"]}
    assert returned_ids == {job.id for job in jobs}
    media = http.get(f"/api/v1/labeling/jobs/{job_id}/media")
    assert media.status_code == 200, media.text
    assert media.json()["total"] > 0
    historical_review = http.get(f"/api/v1/review/jobs/{job_id}")
    assert historical_review.status_code == 200, historical_review.text


def test_shared_monitor_visibility_never_crosses_event_boundary(dataset_ctx):
    http, maker, csrf = dataset_ctx
    dataset_id = ingest(http, csrf)
    teams(http, csrf)
    planned = act(http, csrf, "/labeling/plans", {"dataset_id": dataset_id})
    assert planned.status_code == 201, planned.text
    with maker.begin() as db:
        owner_id = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        db.add(StaffAccount(
            email="cross-event-monitor@example.org",
            name="Event Monitor", roles=["monitor"],
            password_hash=password_hash("EventMonitorPassword123!"),
        ))
        db.add(Event(id="other-event", name="Separate Private Event"))
        db.flush()
        team = CompetitionTeam(event_id="other-event", name="Different Team")
        ds = MediaDataset(event_id="other-event", name="Other Dataset",
                          upload_kind="folder", root_path="/tmp/private-event",
                          created_by=owner_id, status="ready")
        db.add_all((team, ds))
        db.flush()
        plan = LabelingPlan(event_id="other-event", dataset_id=ds.id,
                            jobs_per_team=1, redundancy=1,
                            created_by=owner_id)
        asset = MediaAsset(event_id="other-event", dataset_id=ds.id,
                           relative_path="private.jpg", morphology="bird",
                           kind="image", size_bytes=10, sha256="a" * 64)
        db.add_all((plan, asset))
        db.flush()
        db.add(LabelingJob(
            id="other-event-job", event_id="other-event", dataset_id=ds.id,
            plan_id=plan.id, team_id=team.id, sequence=1,
            media_ids=[asset.id], frame_count=1, video_count=0,
            morphology_counts={"bird": 1},
        ))
    login = http.post("/api/v1/auth/login", json={
        "email": "cross-event-monitor@example.org",
        "password": "EventMonitorPassword123!",
    })
    assert login.status_code == 200
    monitor_csrf = login.json()["csrf_token"]
    # Login defaults to the most recently created active event. Explicitly
    # switch to the original event to verify that the other event stays hidden.
    assert act(http, monitor_csrf, f"/events/{DEFAULT_EVENT_ID}/select").status_code == 200
    jobs = http.get("/api/v1/labeling/jobs?limit=2000")
    assert jobs.status_code == 200
    assert "other-event-job" not in {row["id"] for row in jobs.json()["items"]}
    assert http.get("/api/v1/labeling/jobs/other-event-job/media").status_code == 404
    assert http.get("/api/v1/review/jobs/other-event-job").status_code == 404
    # Switching events changes the authorized scope, never mixing both.
    assert act(http, monitor_csrf, "/events/other-event/select").status_code == 200
    alternate = http.get("/api/v1/labeling/jobs?limit=2000")
    assert alternate.status_code == 200
    assert {row["id"] for row in alternate.json()["items"]} == {"other-event-job"}
