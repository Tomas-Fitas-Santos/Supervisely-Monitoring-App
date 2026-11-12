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
from annotatiathon.work_queue import claim_primary_block, reserve_repeats


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
            db.add_all(m.MediaAsset(
                id=mid, event_id=event, dataset_id=dataset, video_id=video,
                video_group="bird/source", frame_index=30 * i + j + 1,
                relative_path=f"videos/bird/source/{mid}.jpg",
                kind="video_frame", morphology="bird", size_bytes=1, sha256="0" * 64,
            ) for j, mid in enumerate(ids))
            db.add(m.WorkBlock(event_id=event, plan_id=plan, video_id=video, ordinal=i + 1,
                               frame_count=len(ids), media_ids=ids, repeat_media_ids=[]))
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
        db.add_all(m.MediaAsset(
            id=mid, event_id=event, dataset_id=dataset, video_id=video,
            video_group="bird/source", frame_index=91 + index,
            relative_path=f"videos/bird/source/{mid}.jpg",
            kind="video_frame", morphology="bird", size_bytes=1, sha256="0" * 64,
        ) for index, mid in enumerate(ids))
        db.add(m.WorkBlock(event_id=event, plan_id=plan, video_id=video, ordinal=4,
                           frame_count=30, media_ids=ids, repeat_media_ids=[]))
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
        incomplete = db.get(m.LabelingJob, job_id)
        assert incomplete.review_status == "returned_incomplete"
        assert incomplete.status == "needs_correction"
        assert incomplete.correction_round == 1
        restart = db.scalar(select(m.NativeOperation).where(
            m.NativeOperation.kind == "restart_job",
            m.NativeOperation.target_id == job_id,
        ))
        assert restart.status == "pending"
        # Incomplete frames cannot be claimed for quality review or release
        # another job. Simulate the durable restart worker succeeding before
        # submitting the corrected full 30-frame native job.
        other.membership_status = "joined"
        incomplete.status = "in_progress"
        incomplete.review_status = "unreviewed"
        restart.status = "done"
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
    primary_team = f"team-{suffix}-0"
    repeat_team = f"team-{suffix}-1"
    with Session(engine) as db, db.begin():
        for ordinal in range(5, 21):
            frames = [f"repeat-{ordinal}-{i}" for i in range(30)]
            db.add_all(m.MediaAsset(
                id=mid, event_id=event, dataset_id=dataset, video_id=video,
                video_group="bird/source", frame_index=121 + (ordinal - 5) * 30 + index,
                relative_path=f"videos/bird/source/{mid}.jpg",
                kind="video_frame", morphology="bird", size_bytes=1, sha256="0" * 64,
            ) for index, mid in enumerate(frames))
            primary = m.LabelingJob(
                event_id=event, plan_id=plan, dataset_id=dataset, team_id=primary_team,
                sequence=ordinal, assignee_person_id=person, media_ids=frames,
                frame_count=30, video_count=1, morphology_counts={"bird": 30},
                release_mode="shared_primary", status="completed",
            )
            db.add(primary)
            db.flush()
            db.add(m.WorkBlock(event_id=event, plan_id=plan, video_id=video,
                               ordinal=ordinal, frame_count=30, media_ids=frames,
                               repeat_media_ids=frames, job_id=primary.id))
        db.flush()
        reserve_repeats(db, db.get(m.LabelingPlan, plan), [primary_team, repeat_team])
        for packet in db.scalars(select(m.WorkPacket).where(m.WorkPacket.event_id == event)):
            packet.submitted_at = 1

    repeat_barrier = Barrier(2)

    def claim_repeat(i):
        with Session(engine) as db, db.begin():
            repeat_barrier.wait(timeout=10)
            job = claim_primary_block(db, plan, repeat_team, f"person-{suffix}-{i}",
                                      include_repeats=True)
            assert job.release_mode == "shared_repeat"
            return job.id, set(job.media_ids)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(claim_repeat, [2, 3]))
    assert sorted([len(first[1]), len(second[1])]) == [30, 30]
    assert not first[1].intersection(second[1])
    with Session(engine) as db:
        assigned = db.scalars(select(m.WorkBlock).where(
            m.WorkBlock.plan_id == plan, m.WorkBlock.ordinal >= 5,
        )).all()
        assert len(assigned) == 16
        packets = dict([first, second])
        for block in assigned:
            assert block.repeat_team_id == repeat_team
            if block.repeat_job_id is not None:
                assert set(block.repeat_media_ids) == packets[block.repeat_job_id]
        assert sum(block.repeat_job_id is not None for block in assigned) == 2
        assert claim_primary_block(db, plan, primary_team, person, include_repeats=True) is None
    engine.dispose()
