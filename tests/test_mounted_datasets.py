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
