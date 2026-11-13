"""Incremental mounted import keeps committed media and remote identities immutable."""
import io
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_dataset_workflows
from PIL import Image
from sqlalchemy import delete, select
from test_dataset_workflows import act

from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    DatasetPreparedQueue,
    Event,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MountedSourceFile,
    NativeOperation,
    NativeSourceMedia,
)
from annotatiathon.mounted_datasets import process_mounted_index_once
from annotatiathon.mounted_sync import process_sync_once


@pytest.fixture
def api_context(monkeypatch, tmp_path):
    yield from test_dataset_workflows.ctx.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture
def mounted(api_context, monkeypatch, tmp_path):
    client, maker, csrf = api_context
    sources = tmp_path / "sources"
    source = sources / "birds"
    encoded = io.BytesIO()
    Image.new("RGB", (48, 32), "blue").save(encoded, format="JPEG")
    content = encoded.getvalue()

    def add_clip(title, count=2):
        folder = source / "bird" / title
        folder.mkdir(parents=True)
        for ordinal in range(1, count + 1):
            (folder / f"frame_{ordinal:06d}.jpg").write_bytes(content)
        return folder

    add_clip("first")
    settings = SimpleNamespace(nightjar_dataset_source_dir=str(sources),
        local_data_dir=str(tmp_path), max_dataset_assets=250000,
        max_dataset_bytes=150_000_000_000)
    monkeypatch.setattr("annotatiathon.mounted_datasets.get_settings", lambda: settings)
    registered = act(client, csrf, "/datasets/mounted", {
        "folder": "birds", "name": "Mounted birds", "extraction_policy": "all_frames"})
    assert registered.status_code == 202, registered.text
    dsid = registered.json()["id"]
    with maker() as db:
        assert process_mounted_index_once(db)["status"] == "done"
        originals = {a.id: (a.relative_path, a.sha256, a.frame_index) for a in db.scalars(
            select(MediaAsset).where(MediaAsset.dataset_id == dsid))}
    yield client, maker, csrf, dsid, source, content, add_clip, originals


def synchronize(context):
    client, maker, csrf, dsid, *_ = context
    requested = act(client, csrf, f"/datasets/{dsid}/mounted/sync")
    assert requested.status_code == 202, requested.text
    with maker() as db:
        result = process_sync_once(db)
    return result, client.get(f"/api/v1/datasets/{dsid}/mounted/sync").json()


def test_additions_skip_old_bytes_preserve_ids_and_only_queue_new_mirror(mounted, monkeypatch):
    client, maker, csrf, dsid, source, content, add_clip, originals = mounted
    with maker.begin() as db:
        ds = db.get(MediaDataset, dsid)
        ds.native_dataset_id, ds.native_project_id, ds.mirrored_revision = 9001, 9000, 1
        mirror = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset", NativeOperation.target_id == dsid))
        mirror.status = "done"
        for offset, asset_id in enumerate(originals):
            db.add(NativeSourceMedia(event_id=DEFAULT_EVENT_ID, dataset_id=dsid,
                media_id=asset_id, native_dataset_id=9001, native_image_id=offset + 9100))
        db.add(DatasetPreparedQueue(dataset_id=dsid, event_id=DEFAULT_EVENT_ID,
            inventory_revision=1, configuration_revision=1, state="prepared", runs=[], summary={}))
    add_clip("second", 3)
    (source / "bird" / "still.jpg").write_bytes(content)
    from annotatiathon import mounted_datasets
    original_hash = mounted_datasets.sha256_file
    hashed = []

    def tracked_hash(path):
        hashed.append(path)
        assert "first" not in path.parts, "Existing bytes should not be read again."
        return original_hash(path)

    monkeypatch.setattr(mounted_datasets, "sha256_file", tracked_hash)
    request = act(client, csrf, f"/datasets/{dsid}/mounted/sync")
    assert request.status_code == 202
    assert act(client, csrf, f"/datasets/{dsid}/mounted/sync").json() == request.json()
    # No plan may use a half-synchronized inventory.
    assert act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "mode": "shared_30_v1"}).status_code == 409
    # Simulate a worker process dying after it claimed the request.
    with maker.begin() as db:
        op = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "sync_mounted_dataset", NativeOperation.target_id == dsid))
        op.status, op.claimed_at = "running", 1
    with maker() as db:
        result = process_sync_once(db)
    assert result["status"] == "done", result
    assert (result["new_videos"], result["new_frames"], result["new_images"]) == (1, 3, 1)
    assert len(hashed) == 4
    with maker() as db:
        ds = db.get(MediaDataset, dsid)
        assert (ds.frame_count, ds.image_count, ds.video_count, ds.inventory_revision) == (5, 1, 2, 2)
        assert ds.native_dataset_id == 9001 and ds.mirrored_revision == 1
        for aid, identity in originals.items():
            a = db.get(MediaAsset, aid)
            assert (a.relative_path, a.sha256, a.frame_index) == identity
        assert len(list(db.scalars(select(NativeSourceMedia).where(
            NativeSourceMedia.dataset_id == dsid)))) == 2
        assert db.get(DatasetPreparedQueue, dsid).state == "stale"
        mirror = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset", NativeOperation.target_id == dsid))
        assert mirror.status == "pending" and mirror.payload["inventory_revision"] == 2
        assert not list((source.parent.parent / DEFAULT_EVENT_ID / dsid / "media").rglob("*.jpg"))
    # Repeated synchronization is a no-op, including the revision and mirror operation.
    hashed.clear()
    result, status = synchronize(mounted)
    assert result["status"] == "done"
    assert status["new_frames"] == status["new_images"] == status["new_videos"] == 0
    assert status["inventory_revision"] == 2 and not hashed
    assert client.get(f"/api/v1/review/datasets/{dsid}/assets/{next(iter(originals))}/image").content == content


@pytest.mark.parametrize("damage,expected", [
    ("replacement", "existing_source_file_changed"),
    ("missing", "existing_source_file_missing"),
    ("extend", "existing_video_has_new_frames"),
    ("corrupt", "Unreadable frame"),
])
def test_failed_sync_is_atomic_and_retryable(mounted, damage, expected):
    _client, maker, _csrf, dsid, source, content, add_clip, originals = mounted
    first = source / "bird" / "first" / "frame_000001.jpg"
    add_clip("second")
    if damage == "replacement":
        first.write_bytes(b"replacement")
    elif damage == "missing":
        first.unlink()
    elif damage == "extend":
        (first.parent / "frame_000003.jpg").write_bytes(content)
    else:
        (source / "bird" / "second" / "frame_000002.jpg").write_bytes(b"half-copied")
    result, status = synchronize(mounted)
    assert result["status"] == "attention", status
    assert expected in status["error"]
    with maker() as db:
        assert db.get(MediaDataset, dsid).status == "ready"
        assert db.get(MediaDataset, dsid).inventory_revision == 1
        assert set(db.scalars(select(MediaAsset.id).where(MediaAsset.dataset_id == dsid))) == set(originals)
        assert len(list(db.scalars(select(MountedSourceFile).where(
            MountedSourceFile.dataset_id == dsid)))) == 2
    first.write_bytes(content)
    (first.parent / "frame_000003.jpg").unlink(missing_ok=True)
    (source / "bird" / "second" / "frame_000002.jpg").write_bytes(content)
    result, status = synchronize(mounted)
    assert result["status"] == "done", status
    assert status["new_frames"] == 2


def test_touched_existing_content_is_checked_once_then_skipped(mounted, monkeypatch):
    _client, _maker, _csrf, _dsid, source, *_ = mounted
    frame = source / "bird" / "first" / "frame_000001.jpg"
    stat = frame.stat()
    os.utime(frame, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    from annotatiathon import mounted_datasets
    original_hash = mounted_datasets.sha256_file
    reads = []
    monkeypatch.setattr(mounted_datasets, "sha256_file",
        lambda path: (reads.append(path), original_hash(path))[1])
    assert synchronize(mounted)[0]["status"] == "done"
    assert reads == [frame]
    reads.clear()
    assert synchronize(mounted)[0]["status"] == "done"
    assert reads == []


def test_existing_plans_and_read_only_events_block_sync(mounted):
    client, maker, csrf, dsid, *_ = mounted
    with maker.begin() as db:
        ds = db.get(MediaDataset, dsid)
        plan = LabelingPlan(event_id=DEFAULT_EVENT_ID, dataset_id=dsid,
            status="approved", jobs_per_team=0, redundancy=1, created_by=ds.created_by)
        db.add(plan)
    assert act(client, csrf, f"/datasets/{dsid}/mounted/sync").status_code == 409
    with maker.begin() as db:
        db.get(Event, DEFAULT_EVENT_ID).status = "finalizing"
    assert act(client, csrf, f"/datasets/{dsid}/mounted/sync").status_code == 409


def test_closeout_during_sync_prevents_commit(mounted):
    client, maker, csrf, dsid, _source, _content, add_clip, originals = mounted
    add_clip("second")
    assert act(client, csrf, f"/datasets/{dsid}/mounted/sync").status_code == 202
    with maker.begin() as db:
        db.get(Event, DEFAULT_EVENT_ID).status = "finalizing"
    with maker() as db:
        assert process_sync_once(db)["status"] == "attention"
        assert set(db.scalars(select(MediaAsset.id).where(MediaAsset.dataset_id == dsid))) == set(originals)


def test_legacy_import_bootstraps_receipts_from_committed_hashes(mounted):
    _client, maker, _csrf, dsid, *_ = mounted
    with maker.begin() as db:
        db.execute(delete(MountedSourceFile).where(MountedSourceFile.dataset_id == dsid))
    result, status = synchronize(mounted)
    assert result["status"] == "done", status
    assert status["inventory_revision"] == 1 and status["new_frames"] == 0
    with maker() as db:
        assert len(list(db.scalars(select(MountedSourceFile).where(
            MountedSourceFile.dataset_id == dsid)))) == 2


def test_copying_more_frames_during_sync_rejects_entire_addition(mounted, monkeypatch):
    _client, _maker, _csrf, _dsid, _source, content, add_clip, _originals = mounted
    folder = add_clip("second")
    from annotatiathon import mounted_datasets
    original_hash = mounted_datasets.sha256_file

    def copying(path):
        result = original_hash(path)
        (folder / "frame_000003.jpg").write_bytes(content)
        return result

    monkeypatch.setattr(mounted_datasets, "sha256_file", copying)
    result, status = synchronize(mounted)
    assert result["status"] == "attention"
    assert "source_folder_changed_during_sync" in status["error"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires real video decoder")
def test_raw_video_sync_recovers_only_new_decoder_output(mounted, monkeypatch):
    _client, maker, _csrf, dsid, source, _content, _add_clip, originals = mounted
    video_path = source / "bird" / "second.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=48x32:r=2:d=1", "-c:v", "mpeg4", str(video_path)],
        check=True, capture_output=True)
    from annotatiathon import frame_extraction
    original_extract = frame_extraction.extract_full_video

    def interrupted(_source, destination, **_kwargs):
        destination.mkdir(parents=True)
        (destination / "uncommitted.jpg").write_bytes(b"interrupted decoder")
        raise OSError("interrupted decoder")

    monkeypatch.setattr(frame_extraction, "extract_full_video", interrupted)
    assert synchronize(mounted)[0]["status"] == "attention"
    monkeypatch.setattr(frame_extraction, "extract_full_video", original_extract)
    result, status = synchronize(mounted)
    assert result["status"] == "done", status
    assert status["new_videos"] == 1 and status["new_frames"] == 2
    with maker() as db:
        for asset_id in originals:
            assert db.get(MediaAsset, asset_id) is not None
        assets = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dsid, MediaAsset.video_group == "bird/second")))
        assert len(assets) == 2
        root = Path(db.get(MediaDataset, dsid).root_path) / "media"
        assert all((root / a.relative_path).is_file() for a in assets)
        assert not (root / "videos/bird/second/uncommitted.jpg").exists()


def test_abandoned_new_decoder_frames_never_shadow_supplied_additions(mounted):
    client, maker, _csrf, dsid, _source, content, add_clip, _originals = mounted
    add_clip("second")
    with maker() as db:
        abandoned = Path(db.get(MediaDataset, dsid).root_path) / "media/videos/bird/second"
    abandoned.mkdir(parents=True)
    (abandoned / "frame_000001.jpg").write_bytes(b"abandoned decoder output")
    result, status = synchronize(mounted)
    assert result["status"] == "done", status
    with maker() as db:
        asset_id = db.scalar(select(MediaAsset.id).where(
            MediaAsset.dataset_id == dsid, MediaAsset.video_group == "bird/second"))
    assert client.get(f"/api/v1/review/datasets/{dsid}/assets/{asset_id}/image").content == content
    assert not abandoned.exists()
