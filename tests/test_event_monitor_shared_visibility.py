"""Monitors see all event jobs after monitor-to-team assignments are retired."""

import test_dataset_workflows
from sqlalchemy import select
from test_dataset_workflows import act, ingest, teams

from annotatiathon.models import LabelingJob, MonitorAssignment, StaffAccount
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
