"""Durable job-round validation claims and lease/retry safety."""
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from annotatiathon import models as m
from annotatiathon.validation_tasks import (
    claim_validation,
    enqueue_validation,
    finish_validation,
    process_validation_once,
    recover_expired_validations,
)


def setup_queue():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(m.Event(id="event", name="Benchmark"))
        db.add(m.StaffAccount(id="staff", name="Staff", email="staff@example.test",
                              password_hash="not-used", roles=["organiser"]))
        db.flush()
        db.add(m.MediaDataset(id="dataset", event_id="event", name="Source",
                              root_path="/unused", status="ready", upload_kind="folder",
                              created_by="staff"))
        db.add(m.CompetitionTeam(id="team", event_id="event", name="Team"))
        db.add(m.Person(id="person", event_id="event", team_id="team",
                        name="Annotator", email="annotator@example.test",
                        kind="participant"))
        db.flush()
        db.add(m.LabelingPlan(id="plan", event_id="event", dataset_id="dataset",
                              status="running", jobs_per_team=0,
                              redundancy=1, created_by="staff"))
        db.flush()
        db.add(m.LabelingJob(id="job", event_id="event", dataset_id="dataset",
                             plan_id="plan", team_id="team", assignee_person_id="person",
                             sequence=1, release_mode="shared_primary",
                             media_ids=["image1"], frame_count=1, video_count=1,
                             morphology_counts={}, status="on_review"))
        db.add(m.NativeJobBinding(
            event_id="event", job_id="job", person_id="person", native_job_id=100,
        ))
        db.commit()
    return engine


def test_one_task_per_native_round_and_bounded_retries():
    engine = setup_queue()
    try:
        with Session(engine) as db:
            job = db.get(m.LabelingJob, "job")
            assert enqueue_validation(db, job)
            db.commit()
            assert not enqueue_validation(db, job)
            db.commit()
            claimed = claim_validation(db)
            assert claimed is not None
            op_id, job_id, token = claimed
            assert job_id == "job"
            assert claim_validation(db) is None
            assert finish_validation(db, op_id, token, failure=TimeoutError()) is True
            op = db.get(m.NativeOperation, op_id)
            assert op.status == "pending"
            assert op.next_attempt_at > m.epoch()
            assert not enqueue_validation(db, job)
            # A new correction is a new task generation but same logical job.
            op.status = "done"
            job.correction_round = 1
            binding = db.scalar(select(m.NativeJobBinding))
            binding.native_job_id = 101
            db.commit()
            assert enqueue_validation(db, job)
            db.commit()
            assert op.payload == {"correction_round": 1, "native_job_id": 101}
            assert op.attempts == 0
    finally:
        engine.dispose()


def test_validation_lease_recovery_never_replays_remote_jobs():
    engine = setup_queue()
    try:
        with Session(engine) as db:
            db.add(m.NativeOperation(event_id="event", kind="publish_job",
                                     target_id="job", status="running",
                                     claimed_at=m.epoch() - 1000))
            enqueue_validation(db, db.get(m.LabelingJob, "job"))
            db.commit()
            task_id, _job_id, token = claim_validation(db)
            op = db.get(m.NativeOperation, task_id)
            op.lease_until = m.epoch() - 1
            db.commit()
            assert recover_expired_validations(db) == 1
            assert not finish_validation(db, task_id, token)
            assert db.get(m.NativeOperation, task_id).status == "pending"
            native = db.scalar(select(m.NativeOperation).where(
                m.NativeOperation.kind == "publish_job",
            ))
            assert native.status == "running"
    finally:
        engine.dispose()


def test_separate_validator_completes_one_task_and_does_not_requeue(monkeypatch):
    engine = setup_queue()
    try:
        from annotatiathon import native_worker
        called = []

        def fake_capture(_db, _api, job):
            called.append((job.id, job.correction_round))
            # A completed snapshot must not be revisited on the next poll.
            job.annotation_snapshot_round = job.correction_round
            job.annotation_snapshot_frames = job.frame_count
            return True

        monkeypatch.setattr(native_worker, "capture_job_annotations", fake_capture)
        with Session(engine) as db:
            job = db.get(m.LabelingJob, "job")
            job.release_mode = "manual_released"  # skip shared next-block allocator
            assert enqueue_validation(db, job)
            db.commit()
            info = process_validation_once(db, SimpleNamespace(), None)
            assert info["status"] == "done"
            assert called == [("job", 0)]
            assert process_validation_once(db, SimpleNamespace(), None) is None
            assert not enqueue_validation(db, job)
            task = db.scalar(select(m.NativeOperation).where(
                m.NativeOperation.kind == "validate_submission",
            ))
            assert task.status == "done"
            assert task.lease_owner is None
    finally:
        engine.dispose()


def test_postgres_multiple_validators_claim_only_one_round():
    """SKIP LOCKED fences concurrent readers of the exact same job/round."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import pytest

    connection = os.getenv("NIGHTJAR_QUEUE_TEST_URL")
    if not connection:
        pytest.skip("Requires migrated disposable PostgreSQL")
    engine = create_engine(connection)
    suffix = m.identifier()[:8]
    event = f"validate-event-{suffix}"
    staff = f"validate-staff-{suffix}"
    team = f"validate-team-{suffix}"
    person = f"validate-person-{suffix}"
    dataset = f"validate-dataset-{suffix}"
    plan = f"validate-plan-{suffix}"
    jobid = f"validate-job-{suffix}"
    with Session(engine) as db:
        db.add(m.Event(id=event, name="Validation concurrency"))
        db.add(m.StaffAccount(
            id=staff, name="Validation owner", email=f"{staff}@example.test",
            password_hash="disabled", roles=["organiser"],
        ))
        db.flush()
        db.add(m.MediaDataset(
            id=dataset, event_id=event, name="Fixture", root_path="/unused",
            status="ready", upload_kind="folder", created_by=staff,
        ))
        db.add(m.CompetitionTeam(id=team, event_id=event, name="Pair"))
        db.add(m.Person(
            id=person, event_id=event, team_id=team, name="Tester",
            email=f"{person}@example.test", kind="participant",
        ))
        db.flush()
        db.add(m.LabelingPlan(
            id=plan, event_id=event, dataset_id=dataset, status="running",
            jobs_per_team=0, redundancy=1, created_by=staff,
        ))
        db.flush()
        job = m.LabelingJob(
            id=jobid, event_id=event, dataset_id=dataset, plan_id=plan,
            team_id=team, assignee_person_id=person,
            sequence=1, release_mode="shared_primary",
            media_ids=["sample"], frame_count=1, video_count=1,
            morphology_counts={}, status="on_review",
        )
        db.add(job)
        db.add(m.NativeJobBinding(
            event_id=event, job_id=jobid,
            person_id=person, native_job_id=900000 + int(suffix[:4], 16),
        ))
        db.flush()
        assert enqueue_validation(db, job)
        db.commit()

    barrier = Barrier(4)

    def claim(_index):
        with Session(engine) as db:
            barrier.wait(timeout=10)
            return claim_validation(db)

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    owners = [claim for claim in claims if claim is not None]
    assert len(owners) == 1
    assert owners[0][1] == jobid
    with Session(engine) as db:
        assert finish_validation(db, owners[0][0], owners[0][2])
        operation = db.scalar(select(m.NativeOperation).where(
            m.NativeOperation.kind == "validate_submission",
            m.NativeOperation.target_id == jobid,
        ))
        assert operation.status == "done"
        assert operation.attempts == 1
    # An in-progress poll attempting admission after the close transaction
    # must see the new state, even when its SQLAlchemy session cached Active.
    from threading import Event as ThreadEvent

    from annotatiathon.closeout_api import finish_closeout, lock_closeout_event

    poll_started = ThreadEvent()

    def admit_after_close():
        with Session(engine) as db:
            assert db.get(m.Event, event).status == "active"
            job = db.get(m.LabelingJob, jobid)
            job.correction_round += 1
            poll_started.set()
            accepted = enqueue_validation(db, job)
            db.rollback()
            return accepted

    with ThreadPoolExecutor(max_workers=1) as pool, Session(engine) as db:
        closing = lock_closeout_event(db, event)
        future = pool.submit(admit_after_close)
        assert poll_started.wait(timeout=10)
        closing.status = "finalizing"
        closing.closing_by = staff
        closing.closing_at = m.epoch()
        db.commit()
        assert not future.result(timeout=10)
        snapshot = finish_closeout(db, lock_closeout_event(db, event))
        assert snapshot["event_status"] == "archived"
        assert snapshot["verified_winner_available"] is False
        assert db.get(m.LabelingJob, jobid).correction_round == 0
        db.commit()
    engine.dispose()


def test_validated_shared_submission_enqueues_next_block_after_commit(monkeypatch):
    """A finished packet releases exactly one next job without a second status pass."""
    from annotatiathon import native_worker

    engine = setup_queue()
    try:
        with Session(engine) as db:
            plan = db.get(m.LabelingPlan, "plan")
            plan.queue_seed = "benchmark-queue"
            person = db.get(m.Person, "person")
            person.check_in_at = 1
            person.membership_status = "joined"
            person.supervisely_user_id = 100
            person.supervisely_role = "annotator"
            person.native_verified_at = 1
            video = m.MediaVideo(
                event_id="event", dataset_id="dataset", video_group="bird/clip",
                extraction_policy="all_frames",
            )
            db.add(video)
            db.flush()
            media_ids = [f"following-{i}" for i in range(30)]
            for index, media_id in enumerate(media_ids):
                db.add(m.MediaAsset(
                    id=media_id, event_id="event", dataset_id="dataset",
                    video_id=video.id, video_group="bird/clip",
                    frame_index=index + 1, relative_path=f"bird/clip/{media_id}.jpg",
                    kind="video_frame", morphology="bird",
                    size_bytes=1, sha256="a" * 64,
                ))
            db.add(m.WorkBlock(
                event_id="event", plan_id="plan", video_id=video.id, ordinal=1,
                frame_count=30, media_ids=media_ids, repeat_media_ids=[],
            ))
            db.add(m.WorkPacket(
                job_id="job", event_id="event", person_id="person",
            ))
            assert enqueue_validation(db, db.get(m.LabelingJob, "job"))
            db.commit()

        def fake_capture(db, _api, job):
            packet = db.get(m.WorkPacket, job.id)
            packet.submitted_at = m.epoch()
            job.annotation_snapshot_round = job.correction_round
            job.annotation_snapshot_frames = job.frame_count
            return True

        monkeypatch.setattr(native_worker, "capture_job_annotations", fake_capture)
        with Session(engine) as db:
            result = process_validation_once(db, SimpleNamespace(), None)
            assert result["next_assignment_queued"] is True
            assert result["status"] == "done"
            jobs = list(db.scalars(select(m.LabelingJob).where(
                m.LabelingJob.plan_id == "plan",
            )))
            assert len(jobs) == 2
            followup = next(job for job in jobs if job.id != "job")
            assert followup.media_ids == media_ids
            assert followup.assignee_person_id == "person"
            assert db.get(m.WorkPacket, "job").submitted_at is not None
            tasks = list(db.scalars(select(m.NativeOperation)))
            assert {(x.kind, x.status) for x in tasks} == {
                ("validate_submission", "done"), ("publish_job", "pending"),
            }
            # No duplicate successor can be queued by repeated validation.
            assert process_validation_once(db, SimpleNamespace(), None) is None
            assert len(list(db.scalars(select(m.LabelingJob)))) == 2
    finally:
        engine.dispose()



def test_successful_validation_survives_successor_allocation_failure(monkeypatch):
    """A competing allocator must not undo a committed frame validation."""
    from annotatiathon import native_worker, work_queue

    engine = setup_queue()
    try:
        with Session(engine) as db:
            plan = db.get(m.LabelingPlan, "plan")
            plan.queue_seed = "benchmark-queue"
            db.add(m.WorkPacket(job_id="job", event_id="event", person_id="person"))
            assert enqueue_validation(db, db.get(m.LabelingJob, "job"))
            db.commit()

        def fake_capture(db, _api, job):
            db.get(m.WorkPacket, job.id).submitted_at = m.epoch()
            job.annotation_snapshot_round = job.correction_round
            job.annotation_snapshot_frames = job.frame_count
            return True

        def simulate_deadlock(*_args, **_kwargs):
            raise RuntimeError("synthetic successor claim deadlock")

        monkeypatch.setattr(native_worker, "capture_job_annotations", fake_capture)
        monkeypatch.setattr(work_queue, "claim_primary_block", simulate_deadlock)
        with Session(engine) as db:
            result = process_validation_once(db, SimpleNamespace(), None)
            assert result["status"] == "done"
            assert db.get(m.WorkPacket, "job").submitted_at is not None
            task = db.scalar(select(m.NativeOperation))
            assert task.status == "done"
    finally:
        engine.dispose()
