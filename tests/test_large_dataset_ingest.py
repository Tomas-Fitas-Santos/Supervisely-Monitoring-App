"""Large-ingest contracts without transferring gigabytes or touching real services."""
import io
import os
import uuid
import zipfile
from pathlib import Path

import pytest
import test_dataset_workflows as workflows
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from annotatiathon.dataset_indexing import process_index_once
from annotatiathon.models import (
    DatasetStagedFile,
    MediaAsset,
    MediaDataset,
    NativeOperation,
    StaffAccount,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    yield from workflows.ctx.__wrapped__(monkeypatch, tmp_path)


def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (32, 24)).save(output, format="JPEG")
    return output.getvalue()


def create(client, csrf, kind="folder"):
    result = workflows.act(client, csrf, "/datasets/uploads", {
        "name": "Background import", "upload_kind": kind, "extraction_policy": "all_frames",
    })
    assert result.status_code == 201, result.text
    return result.json()["id"]


def upload(client, csrf, dsid, path, payload):
    result = client.put(f"/api/v1/datasets/uploads/{dsid}/chunk",
        params={"relative_path": path, "offset": 0, "total": len(payload)},
        content=payload, headers={"X-CSRF-Token": csrf})
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("kind", ["folder", "zip"])
def test_background_import_survives_browser_close_and_is_shared(ctx, kind):
    client, maker, csrf = ctx
    dsid = create(client, csrf, kind)
    content = jpeg()
    if kind == "zip":
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("birds/species/clip/frame_000001.jpg", content)
        upload(client, csrf, dsid, "source.zip", archive.getvalue())
    else:
        upload(client, csrf, dsid, "birds/species/clip/frame_000001.jpg", content)
    endpoint = f"/datasets/{dsid}/finalize?background=true"
    queued = workflows.act(client, csrf, endpoint)
    assert queued.status_code == 202
    assert queued.json()["status"] == "indexing"
    assert workflows.act(client, csrf, endpoint).json()["status"] == "indexing"
    # No importer or in-process background callback runs during the HTTP request.
    with maker() as db:
        assert db.scalar(select(MediaAsset).where(MediaAsset.dataset_id == dsid)) is None
        assert len(list(db.scalars(select(NativeOperation).where(
            NativeOperation.kind == "index_uploaded_dataset")))) == 1
    assert workflows.act(client, csrf, f"/datasets/{dsid}/finalize").status_code == 409
    assert workflows.act(client, csrf, f"/datasets/{dsid}/interrupt").status_code == 409
    assert workflows.act(client, csrf, f"/datasets/{dsid}", method="DELETE").status_code == 409
    reset = workflows.act(client, csrf, "/settings/reset/request", {"confirmation": "RESET Initial Event"})
    assert reset.status_code == 409 and "indexing" in reset.json()["detail"]
    # The durable indexer works in its own session with no browser File objects.
    with maker() as db:
        result = process_index_once(db)
        assert result["status"] == "done", result
        asset = db.scalar(select(MediaAsset).where(MediaAsset.dataset_id == dsid))
        assert (asset.frame_index, asset.frame_width, asset.frame_height) == (1, 32, 24)
        media_id = asset.id
        mirror = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset", NativeOperation.target_id == dsid))
        assert mirror.status == "pending"
        db.add(StaffAccount(email="second@example.test", name="Second organiser",
            roles=["organiser"], password_hash=password_hash("AnotherStrongPassword!")))
        db.commit()
    with TestClient(client.app) as second:
        assert second.post("/api/v1/auth/login", json={
            "email": "second@example.test", "password": "AnotherStrongPassword!",
        }).status_code == 200
        image = second.get(f"/api/v1/review/datasets/{dsid}/assets/{media_id}/image")
        assert image.status_code == 200 and image.content == content
    with TestClient(client.app) as anonymous:
        assert anonymous.get(
            f"/api/v1/review/datasets/{dsid}/assets/{media_id}/image").status_code == 401


def test_crashed_index_is_rebuilt_from_retained_staging(ctx):
    client, maker, csrf = ctx
    dsid = create(client, csrf)
    upload(client, csrf, dsid, "birds/species/clip/frame_000001.jpg", jpeg())
    workflows.act(client, csrf, f"/datasets/{dsid}/finalize?background=true")
    with maker() as db:
        op = db.scalar(select(NativeOperation).where(NativeOperation.target_id == dsid))
        op.status, op.attempts = "running", 1
        ds = db.get(MediaDataset, dsid)
        (Path(ds.root_path) / "media" / "abandoned.jpg").write_bytes(b"partial output")
        db.commit()
    with maker() as db:
        assert process_index_once(db)["status"] == "done"
        assert not (Path(ds.root_path) / "media" / "abandoned.jpg").exists()
        assert db.get(NativeOperation, op.id).attempts == 2


def test_invalid_frame_fails_locally_and_can_retry_without_reupload(ctx):
    client, maker, csrf = ctx
    dsid = create(client, csrf)
    source_path = "birds/species/clip/frame_000001.jpg"
    upload(client, csrf, dsid, source_path, b"corrupt frame")
    endpoint = f"/datasets/{dsid}/finalize?background=true"
    workflows.act(client, csrf, endpoint)
    with maker() as db:
        assert process_index_once(db)["status"] == "attention"
        ds = db.get(MediaDataset, dsid)
        assert ds.status == "failed"
        stage = Path(ds.root_path) / "stage" / source_path
        assert stage.read_bytes() == b"corrupt frame"
        stage.write_bytes(jpeg())
    assert workflows.act(client, csrf, endpoint).json()["status"] == "indexing"
    with maker() as db:
        assert process_index_once(db)["status"] == "done"


def test_closeout_keeps_its_cutoff_and_indexer_cannot_write_to_archived_event(ctx):
    client, maker, csrf = ctx
    dsid = create(client, csrf)
    upload(client, csrf, dsid, "birds/species/clip/frame_000001.jpg", jpeg())
    workflows.act(client, csrf, f"/datasets/{dsid}/finalize?background=true")
    closed = workflows.act(client, csrf, "/closeout/finalize", {"confirmation": "CLOSE Initial Event"})
    assert closed.status_code == 200 and closed.json()["status"] == "archived"
    with maker() as db:
        assert process_index_once(db)["status"] == "attention"
        assert db.get(MediaDataset, dsid).status == "failed"
        assert db.scalar(select(MediaAsset).where(MediaAsset.dataset_id == dsid)) is None
        assert db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset", NativeOperation.target_id == dsid)) is None


@pytest.mark.skipif(os.name == "nt", reason="large sparse fixture uses Linux filesystem holes")
def test_zip64_archive_larger_than_5_gib_indexes_without_reading_padding(ctx):
    client, maker, csrf = ctx
    dsid = create(client, csrf, "zip")
    with maker() as db:
        ds = db.get(MediaDataset, dsid)
        archive = Path(ds.root_path) / "stage" / "source.zip"
        with archive.open("wb") as stream:
            stream.seek(6 * 1024**3)
            with zipfile.ZipFile(stream, "w", allowZip64=True) as zipped:
                zipped.writestr("birds/species/clip/frame_000001.jpg", jpeg())
        size = archive.stat().st_size
        assert size > 6 * 1024**3
        db.add(DatasetStagedFile(dataset_id=dsid, relative_path="source.zip",
            expected_bytes=size, received_bytes=size))
        ds.source_bytes = size
        db.commit()
    assert workflows.act(client, csrf, f"/datasets/{dsid}/finalize?background=true").json()["status"] == "indexing"
    with maker() as db:
        result = process_index_once(db)
        assert result["status"] == "done", result
        assert db.get(MediaDataset, dsid).frame_count == 1


@pytest.mark.skipif(os.name == "nt", reason="large sparse fixture uses Linux filesystem holes")
def test_chunk_receipt_and_resume_offsets_above_4_gib(ctx):
    client, maker, csrf = ctx
    dsid = create(client, csrf, "zip")
    offset, tail = 6 * 1024**3, b"last bytes"
    with maker() as db:
        ds = db.get(MediaDataset, dsid)
        with (Path(ds.root_path) / "stage" / "source.zip").open("wb") as stream:
            stream.truncate(offset)
        db.add(DatasetStagedFile(dataset_id=dsid, relative_path="source.zip",
            expected_bytes=offset + len(tail), received_bytes=offset))
        ds.source_bytes = offset
        db.commit()
    result = client.put(f"/api/v1/datasets/uploads/{dsid}/chunk",
        params={"relative_path": "source.zip", "offset": offset, "total": offset + len(tail)},
        content=tail, headers={"X-CSRF-Token": csrf})
    assert result.status_code == 200, result.text
    assert result.json()["received_bytes"] == offset + len(tail)
    progress = client.get(f"/api/v1/datasets/{dsid}/upload-status?relative_path=source.zip").json()
    assert progress["file"]["received_bytes"] == offset + len(tail)


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_postgres_running_import_is_reclaimed_only_after_its_owner_exits(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from annotatiathon import datasets_api
    from annotatiathon.dataset_indexing import operation_lock
    from annotatiathon.models import Event

    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    with Session(engine, expire_on_commit=False) as db:
        suffix = uuid.uuid4().hex
        event = Event(name=f"Isolated import lock {suffix}")
        staff = StaffAccount(name="Synthetic", email=f"{suffix}@example.invalid",
                             roles=["organiser"], password_hash="disabled")
        db.add_all([event, staff])
        db.flush()
        ds = MediaDataset(event_id=event.id, name="Crash recovery", upload_kind="folder",
            extraction_policy="all_frames", status="indexing", root_path="", created_by=staff.id)
        db.add(ds)
        db.flush()
        root = tmp_path / event.id / ds.id
        ds.root_path = str(root)
        frame = root / "stage" / "birds" / "species" / "clip" / "frame_000001.jpg"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(jpeg())
        (root / "media").mkdir()
        partial = root / "media" / "partial.jpg"
        partial.write_bytes(b"abandoned")
        db.add(DatasetStagedFile(dataset_id=ds.id, relative_path=frame.relative_to(root / "stage").as_posix(),
            expected_bytes=frame.stat().st_size, received_bytes=frame.stat().st_size))
        op = NativeOperation(event_id=event.id, target_id=ds.id,
            kind="index_uploaded_dataset", status="running", attempts=1)
        db.add(op)
        db.commit()
        settings = datasets_api.get_settings().model_copy(update={"local_data_dir": str(tmp_path)})
        monkeypatch.setattr(datasets_api, "get_settings", lambda: settings)
        with operation_lock(db, op.id) as acquired:
            assert acquired
            with Session(engine) as other:
                assert process_index_once(other) is None
            assert partial.exists()  # A live worker's output must not be cleaned.
        with Session(engine) as other:
            result = process_index_once(other)
            assert result["status"] == "done", result
            assert other.get(NativeOperation, op.id).attempts == 2
            assert other.get(MediaDataset, ds.id).frame_count == 1
        assert not partial.exists()
    engine.dispose()


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_postgres_event_operations_stay_responsive_during_uncommitted_media_inserts():
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from annotatiathon.closeout_api import lock_closeout_event
    from annotatiathon.dataset_indexing import require_active_import_event
    from annotatiathon.events_api import current_event
    from annotatiathon.models import Event

    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    suffix = uuid.uuid4().hex
    with Session(engine, expire_on_commit=False) as setup:
        event = Event(name=f"Responsive imports {suffix}")
        staff = StaffAccount(name="Synthetic", email=f"{suffix}@example.invalid",
                             roles=["organiser"], password_hash="disabled")
        setup.add_all([event, staff])
        setup.flush()
        ds = MediaDataset(event_id=event.id, name="In progress", upload_kind="folder",
            root_path="/synthetic", created_by=staff.id, status="indexing")
        setup.add(ds)
        setup.commit()
        ctx = SimpleNamespace(session=SimpleNamespace(event_id=event.id))
        with Session(engine) as inventory:
            inventory.add(MediaAsset(event_id=event.id, dataset_id=ds.id,
                relative_path="images/bird/1.jpg", morphology="bird", kind="image",
                size_bytes=1, sha256="a" * 64))
            inventory.flush()  # PostgreSQL now holds FK KEY SHARE until rollback.
            with Session(engine) as api:
                api.execute(text("SET LOCAL lock_timeout = '500ms'"))
                with pytest.raises(OperationalError):
                    api.scalar(select(Event).where(Event.id == event.id).with_for_update())
                api.rollback()  # Reproduces the previous global event-lock bottleneck.
                api.execute(text("SET LOCAL lock_timeout = '500ms'"))
                assert current_event(api, ctx, writable=True).id == event.id
                # Mutations still serialize against each other.
                with Session(engine) as competing_api:
                    competing_api.execute(text("SET LOCAL lock_timeout = '500ms'"))
                    with pytest.raises(OperationalError):
                        current_event(competing_api, ctx, writable=True)
                    competing_api.rollback()
                api.rollback()
                # A closeout cutoff must not wait for the bulk inventory transaction.
                api.execute(text("SET LOCAL lock_timeout = '500ms'"))
                lock_closeout_event(api, event.id).status = "finalizing"
                api.commit()
            with pytest.raises(HTTPException, match="read-only"):
                require_active_import_event(inventory, ds)
            inventory.rollback()
    engine.dispose()
