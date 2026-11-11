"""Concurrency regression against a disposable migrated PostgreSQL database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.native_worker import sync_all
from annotatiathon.work_queue import claim_primary_block


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_distinct_claims_and_duplicate_person_requests():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    suffix = m.identifier()[:8]
    event, plan, dataset, video, staff = [f"{kind}-{suffix}" for kind in (
        "event", "plan", "dataset", "video", "staff",
    )]
    with Session(engine) as db, db.begin():
        db.add(m.Event(id=event, name="Concurrent queue test"))
        db.add(m.StaffAccount(id=staff, email=f"{staff}@example.test", name=staff,
                              password_hash="disabled", roles=["organiser"]))
        db.flush()
        db.add(m.MediaDataset(id=dataset, event_id=event, name=dataset, upload_kind="folder",
                              root_path="/test", created_by=staff, status="ready"))
        db.flush()
        db.add(m.LabelingPlan(id=plan, dataset_id=dataset, event_id=event, status="running",
                              jobs_per_team=0, redundancy=1, created_by=staff, queue_seed=suffix))
        db.add(m.MediaVideo(id=video, dataset_id=dataset, event_id=event,
                            video_group="bird/source", extraction_policy="all_frames"))
        db.flush()
        for i in range(2):
            db.add(m.CompetitionTeam(id=f"team-{suffix}-{i}", name=f"Team {i}", event_id=event))
        db.flush()
        for i in range(4):
            db.add(m.Person(id=f"person-{suffix}-{i}", team_id=f"team-{suffix}-{i // 2}",
                            name=f"Person {i}", email=f"{i}@example.test", event_id=event,
                            kind="participant", membership_status="joined", supervisely_user_id=i + 1,
                            supervisely_role="annotator", native_verified_at=1))
        for i in range(3):
            ids = [f"frame-{30 * i + j}" for j in range(30)]
            db.add(m.WorkBlock(event_id=event, plan_id=plan, video_id=video, ordinal=i + 1,
                               frame_count=len(ids), media_ids=ids, repeat_media_ids=ids[:2]))
    barrier = Barrier(4)

    def claim(i):
        with Session(engine) as db, db.begin():
            barrier.wait(timeout=10)
            result = claim_primary_block(db, plan, f"team-{suffix}-{i // 2}", f"person-{suffix}-{i}")
            return tuple(result.media_ids) if result else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, [0, 0, 2, 3]))
    packets = [r for r in results if r is not None]
    assert len(packets) == 3
    assert len({asset for packet in packets for asset in packet}) == 90
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(m.WorkPacket)
                         .where(m.WorkPacket.event_id == event)) == 3
        assert db.scalar(select(func.count()).select_from(m.NativeOperation)
                         .where(m.NativeOperation.event_id == event)) == 3
    person = f"person-{suffix}-0"
    with Session(engine) as db, db.begin():
        job = db.scalar(select(m.LabelingJob).where(
            m.LabelingJob.plan_id == plan, m.LabelingJob.assignee_person_id == person,
        ))
        job_id = job.id
        job.status = "in_progress"
        job.native_dataset_id = 100
        db.add(m.NativeJobBinding(event_id=event, job_id=job_id, person_id=person, native_job_id=101))
        ids = [f"next-{i}" for i in range(30)]
        db.add(m.WorkBlock(event_id=event, plan_id=plan, video_id=video, ordinal=4,
                           frame_count=30, media_ids=ids, repeat_media_ids=ids[:2]))
    native = SimpleNamespace(id=101, dataset_id=100, status="on_review",
                             images_count=30, finished_images_count=15)
    api = SimpleNamespace(labeling_job=SimpleNamespace(get_info_by_id=lambda _: native))
    with Session(engine) as db:
        # Keep the fourth person unverified while testing that the incomplete
        # packet alone cannot claim the available block.
        other = db.get(m.Person, f"person-{suffix}-1")
        other.membership_status = "not_invited"
        db.commit()
        assert sync_all(db, api, m.SuperviselyConfig())["pending_operations"] == 0
        assert db.get(m.WorkPacket, job_id).submitted_at is None
        assert db.get(m.LabelingJob, job_id).review_status == "incomplete_submission"
        other.membership_status = "joined"
        db.commit()
        native.finished_images_count = 30
        assert sync_all(db, api, m.SuperviselyConfig())["pending_operations"] == 1
        assert db.get(m.LabelingJob, job_id).status == "on_review"
        assert db.get(m.WorkPacket, job_id).submitted_at is not None
        successor = db.scalar(select(m.LabelingJob).where(
            m.LabelingJob.plan_id == plan, m.LabelingJob.assignee_person_id == person,
            m.LabelingJob.id != job_id,
        ))
        assert successor.media_ids == ids
        assert sync_all(db, api, m.SuperviselyConfig())["pending_operations"] == 0
    engine.dispose()
