"""Host-mounted source resolution must not copy files or escape the approved mount."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from annotatiathon import mounted_datasets


def _configure(monkeypatch, tmp_path):
    base = tmp_path / "host_sources"
    base.mkdir()
    monkeypatch.setattr(
        mounted_datasets, "get_settings",
        lambda: SimpleNamespace(nightjar_dataset_source_dir=str(base)),
    )
    return base


def test_linked_frames_are_read_from_host_source_without_staging(monkeypatch, tmp_path):
    base = _configure(monkeypatch, tmp_path)
    folder = base / "BirdGaze"
    frame = folder / "Sparrow" / "Clip_01" / "frame_000001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"test-image-bytes")
    private_root = tmp_path / "private"
    (private_root / "media").mkdir(parents=True)
    ds = SimpleNamespace(
        root_path=str(private_root), upload_kind="mounted", source_folder="BirdGaze",
    )
    found = mounted_datasets.media_file(ds, "videos/Sparrow/Clip_01/frame_000001.jpg")
    assert found == frame.resolve()
    assert not list((private_root / "media").rglob("*.jpg"))


def test_species_image_folder_resolves_to_original_file(monkeypatch, tmp_path):
    base = _configure(monkeypatch, tmp_path)
    frame = base / "BirdGaze" / "Sparrow" / "img_0001.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"test-png")
    root = tmp_path / "private"
    (root / "media").mkdir(parents=True)
    ds = SimpleNamespace(root_path=str(root), upload_kind="mounted", source_folder="BirdGaze")
    assert mounted_datasets.media_file(ds, "images/Sparrow/img_0001.png") == frame.resolve()


def test_mounted_folder_traversal_and_symlink_rejected(monkeypatch, tmp_path):
    base = _configure(monkeypatch, tmp_path)
    (base / "BirdGaze").mkdir()
    with pytest.raises(HTTPException):
        mounted_datasets.mounted_source("../outside")
    with pytest.raises(HTTPException):
        mounted_datasets.mounted_source("/tmp")
    link = base / "Linked"
    try:
        link.symlink_to(base / "BirdGaze", target_is_directory=True)
    except OSError:
        pytest.skip("Test filesystem does not support symlink creation")
    with pytest.raises(HTTPException):
        mounted_datasets.mounted_source("Linked")


def test_mounted_path_does_not_escape_source_folder(monkeypatch, tmp_path):
    base = _configure(monkeypatch, tmp_path)
    folder = base / "BirdGaze"
    folder.mkdir()
    private_root = tmp_path / "private"
    (private_root / "media").mkdir(parents=True)
    ds = SimpleNamespace(
        root_path=str(private_root), upload_kind="mounted", source_folder="BirdGaze",
    )
    with pytest.raises(HTTPException):
        mounted_datasets.media_file(ds, "videos/../../outside.jpg")
    with pytest.raises(FileNotFoundError):
        mounted_datasets.media_file(ds, "videos/Sparrow/Clip/missing.jpg")


def test_register_index_review_mounted_frames_without_copying(monkeypatch, tmp_path):
    """End-to-end API registration -> durable indexing -> local review via source mount."""
    import io

    import test_dataset_workflows
    from PIL import Image
    from sqlalchemy import select

    from annotatiathon.models import DEFAULT_EVENT_ID, MediaAsset, NativeOperation

    fixture = test_dataset_workflows.ctx.__wrapped__
    # The shared fixture is a generator; manually run it under pytest's
    # monkeypatch/temp-path fixtures to preserve the existing API setup.
    context = fixture(monkeypatch, tmp_path)
    client, maker, csrf = next(context)
    try:
        source_root = _configure(monkeypatch, tmp_path)
        dataset_folder = source_root / "BirdGaze2026" / "bird" / "clip_01"
        dataset_folder.mkdir(parents=True)
        frame = io.BytesIO()
        Image.new("RGB", (32, 24)).save(frame, format="JPEG")
        for number in (1, 2):
            (dataset_folder / f"frame_{number:06d}.jpg").write_bytes(frame.getvalue())
        monkeypatch.setattr(
            mounted_datasets, "get_settings",
            lambda: SimpleNamespace(
                nightjar_dataset_source_dir=str(source_root),
                local_data_dir=str(tmp_path),
                max_dataset_assets=250000,
                max_dataset_bytes=140_000_000_000,
            ),
        )
        response = client.post("/api/v1/datasets/mounted",
            json={"folder":"BirdGaze2026","name":"BirdGaze mounted","extraction_policy":"all_frames"},
            headers={"X-CSRF-Token":csrf})
        assert response.status_code == 202, response.text
        dataset_id = response.json()["id"]
        assert response.json()["status"] == "indexing"
        with maker() as db:
            done = mounted_datasets.process_mounted_index_once(db)
        assert done["status"] == "done", done
        response = client.get("/api/v1/datasets/"+dataset_id+"/summary")
        assert response.status_code == 200
        assert response.json()["total_assets"] == 2
        with maker() as db:
            assets = list(db.scalars(select(MediaAsset).where(
                MediaAsset.dataset_id == dataset_id)))
            assert len(assets) == 2
            mirror = db.scalar(select(NativeOperation).where(
                NativeOperation.kind == "mirror_dataset",
                NativeOperation.target_id == dataset_id,
            ))
            assert mirror is not None and mirror.status == "pending"
        assert sorted(asset.frame_index for asset in assets) == [1, 2]
        assert not list((tmp_path / DEFAULT_EVENT_ID / dataset_id / "media").rglob("*.jpg"))
        preview = client.get(
            "/api/v1/review/datasets/"+dataset_id+"/assets/"+assets[0].id+"/image")
        assert preview.status_code == 200
        assert preview.content == frame.getvalue()
    finally:
        try:
            next(context)
        except StopIteration:
            pass


def test_windows_source_picker_reuses_existing_live_stack_safely():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    helper = (root / "scripts" / "link-local-dataset.ps1").read_text(encoding="utf-8")
    compose = (root / "compose.live.yaml").read_text(encoding="utf-8")
    ui = (root / "web" / "src" / "mounted_dataset_library.tsx").read_text(encoding="utf-8")

    assert "FolderBrowserDialog" in helper
    assert "DatasetFolder" in helper
    assert "NIGHTJAR_DATASET_HOST_DIR" in helper
    assert "source-datasets" in helper
    assert "up -d --no-deps --force-recreate api" in helper
    assert "up -d --no-deps --force-recreate web" in helper
    assert "up -d --no-deps --force-recreate worker" in helper
    assert "docker compose @compose down" not in helper
    assert not any("--volumes" in line and not line.lstrip().startswith("#")
                   for line in helper.splitlines())
    assert "read_only: true" in compose
    assert "No dataset folders visible to Nightjar." in ui
    assert "link-local-dataset.ps1" in ui
