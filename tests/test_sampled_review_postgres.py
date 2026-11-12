"""Concurrent review claims against a disposable, migrated PostgreSQL database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.auth import AuthContext
from annotatiathon.review_queue_api import claim_next


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_two_monitors_claim_distinct_samples_from_global_random_pool():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    event_id = m.identifier()
    with Session(engine) as db, db.begin():
        event = m.Event(id=event_id, name="Concurrent review")
        monitors = [
            m.StaffAccount(email=f"{event_id}-{i}@example.test", name=f"Monitor {i}",
                           password_hash="disabled", roles=["monitor"])
            for i in range(3)
        ]
        db.add_all([event, *monitors])
        db.flush()
        monitor_ids = [monitor.id for monitor in monitors]
        dataset = m.MediaDataset(event_id=event_id, name="Review fixtures", upload_kind="folder",
                                 root_path="/test", created_by=monitor_ids[0], status="ready")
        db.add(dataset)
        db.flush()
        plan = m.LabelingPlan(event_id=event_id, dataset_id=dataset.id, jobs_per_team=0,
                              redundancy=1, created_by=monitor_ids[0])
        db.add(plan)
        db.flush()
        for i in range(2):
            team = m.CompetitionTeam(event_id=event_id, name=f"Team {i}")
            db.add(team)
            db.flush()
            person = m.Person(event_id=event_id, team_id=team.id, kind="participant",
                              name=f"Annotator {i}", email=f"{i}@example.test")
            db.add(person)
            db.flush()
            asset = m.MediaAsset(event_id=event_id, dataset_id=dataset.id,
                                 relative_path=f"{i}.jpg", morphology="bird", kind="image",
                                 size_bytes=1, sha256="0" * 64)
            db.add(asset)
            db.flush()
            for sequence in range(1, 3):
                job = m.LabelingJob(event_id=event_id, team_id=team.id, plan_id=plan.id,
                                    dataset_id=dataset.id, sequence=sequence,
                                    assignee_person_id=person.id, media_ids=[asset.id],
                                    frame_count=1, video_count=1, morphology_counts={"bird": 1},
                                    status="on_review")
                db.add(job)
                db.flush()
                db.add(m.ReviewSample(event_id=event_id, team_id=team.id, job_id=job.id,
                                      media_id=asset.id, correction_round=0, snapshot={
                                          "source_relative_path": asset.relative_path,
                                          "sha256": asset.sha256, "selection_reason": "routine",
                                          "annotation": {"objects": []},
                                      }))

    def claim(monitor_id, barrier):
        with Session(engine, expire_on_commit=False) as db:
            ctx = AuthContext(db.get(m.StaffAccount, monitor_id), m.StaffSession(event_id=event_id))
            barrier.wait(timeout=10)
            return claim_next(ctx, db)["review"]

    def simultaneous(ids):
        barrier = Barrier(len(ids))
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            return list(pool.map(lambda monitor_id: claim(monitor_id, barrier), ids))

    first = simultaneous(monitor_ids[:2])
    assert len({review["review_id"] for review in first}) == 2
    assert {review["team_name"] for review in first} <= {"Team 0", "Team 1"}
    # A global random pool permits both claims to come from the same team.
    repeated = simultaneous([monitor_ids[2], monitor_ids[2]])
    assert repeated[0]["review_id"] == repeated[1]["review_id"]
    assert repeated[0]["claim_token"] == repeated[1]["claim_token"]
    with Session(engine) as db:
        active = db.scalars(select(m.ReviewSample).where(
            m.ReviewSample.event_id == event_id, m.ReviewSample.reviewer_id.is_not(None),
        )).all()
        assert len(active) == 3
        assert {sample.reviewer_id for sample in active} == set(monitor_ids)
    engine.dispose()
