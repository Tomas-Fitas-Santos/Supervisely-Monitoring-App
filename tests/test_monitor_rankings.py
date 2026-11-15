"""Reviewer contributions use saved historical work; no remote services involved."""
from datetime import datetime

import pytest
from sqlalchemy import func, select
from test_native_sync import context, call

from annotatiathon import models as m

NOW = int(datetime.fromisoformat("2026-07-15T23:30:00+00:00").timestamp())
LISBON_MIDNIGHT = NOW - 1800


@pytest.fixture
def reviewed(context, monkeypatch):
    http, maker, csrf = context
    monkeypatch.setattr("annotatiathon.monitor_rankings.epoch", lambda: NOW)
    with maker.begin() as db:
        owner = db.scalar(select(m.StaffAccount).where(m.StaffAccount.email == "owner@example.org"))
        monitor = db.scalar(select(m.StaffAccount).where(m.StaffAccount.email == "monitor@example.org"))
        retired = m.StaffAccount(name="Retired monitor", email="retired@example.org", roles=["monitor"],
                                 password_hash=owner.password_hash, active=False)
        db.add(retired)
        db.flush()
        ds = m.MediaDataset(event_id=m.DEFAULT_EVENT_ID, name="Mixed sources", upload_kind="folder",
                            status="ready", root_path="unused", created_by=owner.id)
        team = m.CompetitionTeam(event_id=m.DEFAULT_EVENT_ID, name="Review team")
        db.add_all([ds, team])
        db.flush()
        plan = m.LabelingPlan(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id, jobs_per_team=1,
                              redundancy=1, created_by=owner.id)
        db.add(plan)
        db.flush()
        assets = []
        for index in range(6):
            asset = m.MediaAsset(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id,
                                 relative_path=f"photos/{index}.jpg", kind="image" if index%2==0 else "video_frame",
                                 morphology="birds", sha256="a"*64, size_bytes=100)
            db.add(asset)
            assets.append(asset)
        db.flush()
        job = m.LabelingJob(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id, plan_id=plan.id,
                            team_id=team.id, sequence=1, media_ids=[a.id for a in assets],
                            frame_count=6, video_count=1, morphology_counts={"birds":6}, status="on_review")
        db.add(job)
        db.flush()
        def sample(asset, reviewer, completed, correction_round=0, score=5, historical=False):
            titles = ["bird", "left_eye", "right_eye_occ", "beak", "crown"]
            decision = {"verdicts":[{"object_index":i, "correct":i<score} for i in range(5)]}
            if not historical:
                decision["frame_points"] = score
            db.add(m.ReviewSample(event_id=m.DEFAULT_EVENT_ID, job_id=job.id, team_id=team.id,
                                 media_id=asset.id, correction_round=correction_round,
                                 reviewer_id=reviewer, completed_at=completed,
                                 expires_at=NOW-60, decision=decision if completed else None,
                                 snapshot={"annotation":{"objects":[{"classTitle":title} for title in titles]}}))
        sample(assets[0], owner.id, LISBON_MIDNIGHT-1)
        sample(assets[0], owner.id, NOW-10, correction_round=1, score=3)  # same image reviewed again
        sample(assets[1], owner.id, NOW-9, score=0)
        sample(assets[2], monitor.id, NOW-8, score=2, historical=True)
        sample(assets[3], monitor.id, NOW-7, score=4)
        sample(assets[4], owner.id, None)  # released/expired claim
        sample(assets[5], None, NOW-6)  # unattributed historical row cannot be guessed
        sample(assets[5], monitor.id, None, correction_round=1)  # unfinished claim
        db.add(m.JobReview(event_id=m.DEFAULT_EVENT_ID, job_id=job.id, media_id=assets[4].id,
                           reviewer_id=retired.id, verdict="good", reviewed_at=LISBON_MIDNIGHT-2))
        ids = {"owner":owner.id, "monitor":monitor.id, "retired":retired.id, "job":job.id}
    return http, maker, csrf, ids


def test_historical_rankings_unique_images_ties_breakdown_and_no_mutations(reviewed):
    http, maker, _, ids = reviewed
    before = None
    with maker() as db:
        before = (db.scalar(select(func.count()).select_from(m.ReviewSample)),
                  db.scalar(select(func.count()).select_from(m.AuditEvent)))
    for _ in range(2):
        response = http.get("/api/v1/rankings/monitors")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["reviewed_images"] == 5
        assert result["completed_reviews"] == 6
        assert result["reviewer_count"] == 3
        assert [r["rank"] for r in result["items"]] == [1,1,3]
        rows = {r["reviewer_id"]:r for r in result["items"]}
        assert rows[ids["owner"]]["reviewed_images"] == 2
        assert rows[ids["owner"]]["completed_reviews"] == 3
        assert rows[ids["owner"]]["video_frames"] == rows[ids["owner"]]["still_images"] == 1
        assert rows[ids["monitor"]]["last_reviewed_at"] == NOW-7
        assert rows[ids["retired"]]["reviewed_images"] == 1
        assert "email" not in response.text and "password" not in response.text
    with maker() as db:
        assert before == (db.scalar(select(func.count()).select_from(m.ReviewSample)),
                          db.scalar(select(func.count()).select_from(m.AuditEvent)))


def test_today_uses_lisbon_midnight_including_daylight_saving(reviewed):
    http, _, _, _ = reviewed
    result = http.get("/api/v1/rankings/monitors?period=today").json()
    assert result["period_start"] == LISBON_MIDNIGHT
    assert result["completed_reviews"] == result["reviewed_images"] == 4
    assert result["reviewer_count"] == 2
    assert http.get("/api/v1/rankings/monitors?period=unknown").status_code == 422


def test_average_scores_include_zero_repeat_reviews_and_recover_historical_scores(reviewed):
    http, _, _, ids = reviewed
    rows = {r["reviewer_id"]:r for r in http.get("/api/v1/rankings/monitors").json()["items"]}
    assert rows[ids["owner"]]["average_review_score"] == pytest.approx(8/3)
    assert rows[ids["owner"]]["scored_reviews"] == 3
    assert rows[ids["monitor"]]["average_review_score"] == 3
    assert rows[ids["monitor"]]["scored_reviews"] == 2
    assert rows[ids["retired"]]["average_review_score"] is None
    assert rows[ids["retired"]]["scored_reviews"] == 0
    today = {r["reviewer_id"]:r for r in http.get("/api/v1/rankings/monitors?period=today").json()["items"]}
    assert today[ids["owner"]]["average_review_score"] == 1.5
    assert today[ids["owner"]]["scored_reviews"] == 2


def test_unrecoverable_scores_are_excluded_without_changing_review_counts(reviewed):
    http, maker, _, ids = reviewed
    with maker.begin() as db:
        samples = list(db.scalars(select(m.ReviewSample).where(
            m.ReviewSample.reviewer_id == ids["owner"], m.ReviewSample.completed_at.is_not(None),
        ).order_by(m.ReviewSample.completed_at)))
        samples[0].decision = {"saved":True}  # no immutable per-object verdicts
        samples[1].decision = {"frame_points":None, "verdicts":None}
    result = http.get("/api/v1/rankings/monitors").json()
    row = next(r for r in result["items"] if r["reviewer_id"] == ids["owner"])
    assert row["completed_reviews"] == 3 and row["reviewed_images"] == 2
    assert row["scored_reviews"] == 1 and row["average_review_score"] == 0
    with maker() as db:
        assert db.get(m.ReviewSample, samples[0].id).decision == {"saved":True}


def test_superseded_work_stays_credited_and_events_are_isolated(reviewed):
    http, maker, csrf, ids = reviewed
    with maker.begin() as db:
        job = db.get(m.LabelingJob, ids["job"])
        job.status, job.correction_round = "needs_correction", 9
    assert http.get("/api/v1/rankings/monitors").json()["completed_reviews"] == 6
    created = call(http, csrf, "/events", {"name":"Other event"})
    assert created.status_code == 201
    result = http.get("/api/v1/rankings/monitors").json()
    assert result["items"] == [] and result["completed_reviews"] == 0
    assert call(http, csrf, f"/events/{m.DEFAULT_EVENT_ID}/select").status_code == 200
    with maker.begin() as db:
        db.get(m.Event, m.DEFAULT_EVENT_ID).status = "archived"
    assert http.get("/api/v1/rankings/monitors").json()["completed_reviews"] == 6


def test_monitor_access_and_other_roles_denied(reviewed):
    http, maker, _, _ = reviewed
    login = http.post("/api/v1/auth/login", json={"email":"monitor@example.org", "password":"StrongMonitorPassword!"})
    assert login.status_code == 200
    assert http.get("/api/v1/rankings/monitors").status_code == 200
    with maker.begin() as db:
        monitor = db.scalar(select(m.StaffAccount).where(m.StaffAccount.email == "monitor@example.org"))
        monitor.roles = ["reception"]
    assert http.get("/api/v1/rankings/monitors").status_code == 403
    http.cookies.clear()
    assert http.get("/api/v1/rankings/monitors").status_code == 401
