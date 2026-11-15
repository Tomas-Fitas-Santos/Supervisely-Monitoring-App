"""Crash boundaries use a fake remote service; PostgreSQL tests use a disposable DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from test_native_dispatch import call, launch
from test_native_dispatch import configured as configured  # noqa: PLC0414 - pytest fixture export
from test_validation_task_queue import setup_queue

from annotatiathon import models as m
from annotatiathon import native_worker as worker
from annotatiathon.dataset_indexing import operation_lock
from annotatiathon.native_recovery import GUARDED_WRITE, recover_abandoned_job_writes


@pytest.mark.parametrize("crash_point", ["before_remote", "after_remote", "before_local_commit"])
def test_publish_process_death_preserves_assignment_and_reconciles(configured, monkeypatch, crash_point):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    create = sdk.labeling_job.create

    def interrupted_create(**kwargs):
        if crash_point == "after_remote":
            create(**kwargs)
        raise SystemExit("simulated process death")

    if crash_point != "before_local_commit":
        monkeypatch.setattr(sdk.labeling_job, "create", interrupted_create)
    with maker() as db:
        job = db.scalar(select(m.LabelingJob).where(m.LabelingJob.sequence == 1))
        identity = (job.id, job.plan_id, job.team_id, list(job.media_ids))
        opid = db.scalar(select(m.NativeOperation.id).where(m.NativeOperation.kind == "publish_job"))
        if crash_point == "before_local_commit":
            commit = db.commit

            def interrupted_commit():
                if db.get(m.NativeOperation, opid).status == "done":
                    raise SystemExit("crash before atomic completion")
                commit()

            monkeypatch.setattr(db, "commit", interrupted_commit)
        with pytest.raises(SystemExit):
            worker.process_once(db, sdk, db.get(m.SuperviselyConfig, 1))
    monkeypatch.setattr(sdk.labeling_job, "create", create)
    with maker() as db:
        op = db.get(m.NativeOperation, opid)
        assert op.status == "running" and op.lease_owner == GUARDED_WRITE
        assert recover_abandoned_job_writes(db) == 1
        assert recover_abandoned_job_writes(db) == 0
        job = db.get(m.LabelingJob, identity[0])
        assert (job.id, job.plan_id, job.team_id, job.media_ids) == identity
        assert db.get(m.LabelingPlan, plan_id).status == "running"
        assert db.get(m.MediaDataset, "dataset01").status == "ready"
        assert db.scalar(select(func.count()).select_from(m.NativeJobBinding)) == 0
    retry = call(http, csrf, "/native/operations/" + opid + "/retry")
    assert retry.status_code == 200, retry.text
    with maker() as db:
        assert worker.process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert db.get(m.NativeOperation, opid).attempts == 2
        assert len(sdk.jobs) == 2  # the legacy fixture has two assignees
        assert db.scalar(select(func.count()).select_from(m.NativeJobBinding)) == 2
        assert db.scalar(select(func.count()).select_from(m.MediaAsset)) == 3
        assert db.scalar(select(m.LabelingJob).where(m.LabelingJob.sequence == 2)).status == "queued"


def test_recovery_never_claims_unmarked_old_workers_or_other_operation_types():
    engine = setup_queue()
    try:
        with Session(engine) as db:
            for kind in ("publish_job", "restart_job", "reassign_job", "validate_submission", "reset_event"):
                db.add(m.NativeOperation(event_id="event", kind=kind, target_id="job",
                    status="running", claimed_at=1,
                    lease_owner=None if kind == "publish_job" else GUARDED_WRITE))
            db.commit()
            assert recover_abandoned_job_writes(db) == 2
            assert recover_abandoned_job_writes(db) == 0
            for op in db.scalars(select(m.NativeOperation)):
                assert op.status == ("attention" if op.kind in {"restart_job", "reassign_job"} else "running")
            assert db.get(m.LabelingJob, "job").status == "on_review"
    finally:
        engine.dispose()


def test_recovery_does_not_require_supervisely_credentials(monkeypatch):
    from sqlalchemy.orm import sessionmaker
    engine = setup_queue()
    try:
        with Session(engine) as db, db.begin():
            db.add(m.NativeOperation(event_id="event", kind="publish_job", target_id="job",
                                     status="running", lease_owner=GUARDED_WRITE))
        monkeypatch.setattr(worker, "session_factory", lambda: sessionmaker(bind=engine))
        assert worker._worker_cycle("recovery")
        with Session(engine) as db:
            assert db.scalar(select(m.NativeOperation.status)) == "attention"
    finally:
        engine.dispose()


@pytest.mark.parametrize("role", ["publish", "recovery", "all"])
def test_worker_scheduler_survives_database_outage(monkeypatch, role):
    class EndTest(BaseException):
        pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    checks = []

    def reset_pending(db):
        checks.append(True)
        if len(checks) == 1:
            raise ConnectionError("temporary unavailable database")
        return False

    cycles = []

    def cycle(lane):
        cycles.append(lane)
        raise EndTest()

    sleeps = []
    monkeypatch.setattr(worker, "session_factory", lambda: FakeSession)
    monkeypatch.setattr(worker, "reset_pending", reset_pending)
    monkeypatch.setattr(worker, "worker_cycle", cycle)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    with pytest.raises(EndTest):
        worker.run_worker(once=False, poll_seconds=3, role=role)
    assert len(checks) == 2
    assert sleeps == [3]
    assert cycles


def test_old_attention_is_visible_and_counts_include_full_history(configured):
    http, maker, _csrf, _plan_id, _sdk = configured
    with maker.begin() as db:
        db.add(m.NativeOperation(id="old-failure", event_id=m.DEFAULT_EVENT_ID,
            kind="publish_job", target_id="old-job", status="attention", created_at=1))
        db.add_all([m.NativeOperation(event_id=m.DEFAULT_EVENT_ID,
            kind="publish_job", target_id=f"history-{i}", status="done", created_at=10+i)
            for i in range(1005)])
    response = http.get("/api/v1/native/overview")
    assert response.status_code == 200
    data = response.json()
    assert len(data["operations"]) == 1000
    assert data["operations"][0]["id"] == "old-failure"
    assert data["operation_counts"]["done"] == 1006  # includes the source mirror
    assert data["operation_counts"]["attention"] == 1


def hold_operation_in_child(url, operation_id, pipe):
    """A separate process owns a committed task until forcibly terminated."""
    engine = create_engine(url)
    with Session(engine) as db, operation_lock(db, operation_id) as acquired:
        assert acquired
        op = db.get(m.NativeOperation, operation_id)
        op.status, op.lease_owner, op.claimed_at = "running", GUARDED_WRITE, 1
        db.commit()
        pipe.send("locked")
        pipe.recv()


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_killed_worker_releases_lock_and_concurrent_recovery_runs_once():
    url = os.environ["NIGHTJAR_QUEUE_TEST_URL"]
    engine = create_engine(url)
    event, operation_id = m.identifier(), m.identifier()
    with Session(engine) as db, db.begin():
        db.add(m.Event(id=event, name=f"Isolated crash recovery {event}"))
        db.flush()
        db.add(m.NativeOperation(id=operation_id, event_id=event, kind="publish_job",
                                 target_id=m.identifier(), status="pending"))
    context = get_context("spawn")
    parent_pipe, child_pipe = context.Pipe()
    child = context.Process(target=hold_operation_in_child, args=(url, operation_id, child_pipe))
    child.start()
    try:
        assert parent_pipe.poll(20), "child failed to claim task"
        assert parent_pipe.recv() == "locked"
        with Session(engine) as db:
            assert recover_abandoned_job_writes(db) == 0
            assert db.get(m.NativeOperation, operation_id).status == "running"
        child.terminate()
        child.join(10)
        assert not child.is_alive()
        barrier = Barrier(2)

        def recover():
            with Session(engine) as db:
                barrier.wait(timeout=10)
                return recover_abandoned_job_writes(db)

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(lambda _: recover(), range(2))) == 1
        with Session(engine) as db:
            assert db.get(m.NativeOperation, operation_id).status == "attention"
            assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                m.AuditEvent.target_id == operation_id)) == 1
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)
        parent_pipe.close()
        child_pipe.close()
        engine.dispose()
