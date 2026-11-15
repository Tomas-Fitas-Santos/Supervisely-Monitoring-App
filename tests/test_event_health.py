import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_native_dispatch import configured as configured  # noqa: PLC0414
from test_validation_task_queue import setup_queue

from annotatiathon import event_health as health
from annotatiathon import models as m
from annotatiathon import worker_health as telemetry

NOW = 1800000000


@pytest.fixture
def environment(tmp_path, monkeypatch):
    settings = SimpleNamespace(local_data_dir=str(tmp_path))
    monkeypatch.setattr(health, "get_settings", lambda: settings)
    monkeypatch.setattr(telemetry, "get_settings", lambda: settings)
    monkeypatch.setenv("NIGHTJAR_EXPECTED_WORKERS", '{"publish":1}')
    engine = setup_queue()
    yield engine, tmp_path
    engine.dispose()


def add_op(db, name, **kwargs):
    row = m.NativeOperation(event_id="event", kind="validate_submission", target_id=name,
                            created_at=NOW-10000, **kwargs)
    db.add(row)
    return row


def test_queue_delay_excludes_backoff_and_uses_latest_validation_schedule(environment):
    engine, _ = environment
    with Session(engine) as db:
        add_op(db, "ready", status="pending", next_attempt_at=NOW-150)
        add_op(db, "backoff", status="pending", next_attempt_at=NOW+60)
        add_op(db, "running", status="running", claimed_at=NOW-320, lease_until=NOW-1)
        add_op(db, "done", status="done", claimed_at=NOW-140, completed_at=NOW-100)
        add_op(db, "old", status="done", claimed_at=NOW-2000, completed_at=NOW-1000)
        db.commit()
        q = health.queue_health(db, "event", NOW)[0]
        assert (q["ready"], q["scheduled"], q["running"], q["finished_15m"]) == (1, 1, 1, 1)
        assert q["wait_seconds"] == 150  # not the first ever validation's creation time
        assert q["run_seconds"] == 320 and q["expired_leases"] == 1
        assert q["average_run_seconds"] == 40 and q["estimated_drain_seconds"] == 900
        assert q["state"] == "warning"
        assert health.queue_health(db, "different-event", NOW) == []


@pytest.mark.parametrize(("status", "delay", "expected"), [("pending", 600, "critical"),
    ("pending", 119, "ok"), ("attention", 0, "critical"), ("pending", 120, "warning")])
def test_congestion_thresholds_and_no_invented_drain_estimate(environment, status, delay, expected):
    engine, _ = environment
    with Session(engine) as db:
        add_op(db, "task", status=status, next_attempt_at=NOW-delay)
        db.commit()
        row = health.queue_health(db, "event", NOW)[0]
        assert row["state"] == expected
        assert row["estimated_drain_seconds"] is None


def test_replaced_worker_does_not_count_as_missing_but_hung_cycle_does(environment, monkeypatch):
    _, root = environment
    monkeypatch.setattr(telemetry.time, "time", lambda: NOW)
    telemetry.report_cycle("publish", "working")
    telemetry.finish_cycle("publish")
    folder = root / "operations-health" / "workers"
    (folder / "old.json").write_text(json.dumps({"role": "publish", "state": "working", "updated_at": NOW-900}))
    (folder / "bad.json").write_text("{")
    rows, unreadable = telemetry.worker_status(NOW, {"publish": 1})
    assert rows[0]["state"] == "ok" and unreadable == 1
    assert telemetry.worker_status(NOW+91, {"publish": 1})[0][0]["state"] == "warning"
    telemetry.report_cycle("publish", "working")
    hung = telemetry.worker_status(NOW+301, {"publish": 1})[0][0]
    assert hung["longest_unfinished_seconds"] == 301


def test_failed_telemetry_cannot_interrupt_worker_result(environment, monkeypatch):
    from annotatiathon import native_worker
    monkeypatch.setattr(telemetry, "health_directory", lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(native_worker, "observed_worker_cycle", lambda lane: True)
    assert native_worker.worker_cycle("publish") is True


def test_worker_error_is_not_reported_as_successful_cycle(environment, monkeypatch):
    monkeypatch.setattr(telemetry.time, "time", lambda: NOW)
    telemetry.report_cycle("publish", "working")
    telemetry.report_cycle("publish", "error")
    telemetry.finish_cycle("publish")
    row = telemetry.worker_status(NOW, {"publish": 1})[0][0]
    assert row["errors"] == 1 and row["state"] == "warning"


def test_backup_receipt_unknown_stale_failed_and_current(environment):
    _, root = environment
    assert health.backup_health(NOW)["state"] == "unknown"
    folder = root / "operations-health"
    folder.mkdir(exist_ok=True)
    receipt = folder / "backup.json"
    from datetime import UTC, datetime
    receipt.write_text(json.dumps({"completed_utc": datetime.fromtimestamp(NOW-30, UTC).isoformat()}))
    assert health.backup_health(NOW)["state"] == "ok"
    assert health.backup_health(NOW)["independent_storage"] == "unverified"
    assert health.backup_health(NOW+1000)["state"] == "warning"
    receipt.write_text(json.dumps({"completed_utc": datetime.fromtimestamp(NOW-30, UTC).isoformat(),
                                   "last_attempt_failed": True}))
    assert health.backup_health(NOW)["state"] == "warning"


def test_health_detects_missing_media_and_stale_sync_without_mutating(environment, monkeypatch):
    engine, _ = environment
    monkeypatch.setattr(health.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024, total=100000))
    with Session(engine) as db:
        db.add(m.MediaAsset(id="missing", event_id="event", dataset_id="dataset", kind="image",
            relative_path="missing.jpg", morphology="unknown", size_bytes=3, sha256="a"*64))
        db.commit()
        result = health.event_health(db, db.get(m.Event, "event"), now=NOW)
        assert result["state"] == "critical"
        assert result["datasets"][0]["missing"] == 1
        assert result["sync"]["never_synced"] == 1
        assert not db.new and not db.dirty and not db.deleted
        assert db.get(m.LabelingJob, "job").status == "on_review"
        assert "root_path" not in json.dumps(result)


def test_health_endpoint_is_organiser_only_and_never_calls_sdk(configured):
    http, maker, _csrf, _plan, sdk = configured
    before = (len(sdk.jobs), len(sdk.images), len(sdk.upload_history))
    response = http.get("/api/v1/event-health")
    assert response.status_code == 200, response.text
    assert before == (len(sdk.jobs), len(sdk.images), len(sdk.upload_history))
    with maker.begin() as db:
        db.scalar(select(m.StaffAccount)).roles = ["monitor"]
    assert http.get("/api/v1/event-health").status_code == 403
