"""Protect the original frame-upload route and its full-frame defaults."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_classic_dataset_upload_is_primary_and_mounted_path_is_optional():
    workspace = (ROOT / "web" / "src" / "dataset_workspace.tsx").read_text(
        encoding="utf-8"
    )
    assert 'import { DatasetScreen } from "./datasets";' in workspace
    assert 'import { MountedDatasetLibrary } from "./mounted_dataset_library";' in workspace
    assert 'useState<"upload"|"mount"|"coverage">("upload")' in workspace
    assert "Upload folder or ZIP" in workspace
    assert "Link host folder (optional)" in workspace
    assert "<DatasetScreen auth={auth}/>" in workspace
    assert "<MountedDatasetLibrary auth={auth}/>" in workspace


def test_browser_folder_zip_import_preserves_full_frame_source_identity():
    screen = (ROOT / "web" / "src" / "datasets.tsx").read_text(encoding="utf-8")
    app = (ROOT / "src" / "annotatiathon" / "app.py").read_text(encoding="utf-8")
    assert 'useState<"all_frames"|"sample_1fps">("all_frames")' in screen
    assert '"upload_kind:mode"' not in screen  # TypeScript request uses an object value
    assert "upload_kind:mode,extraction_policy:extractionPolicy" in screen
    assert 'webkitdirectory:""' in screen
    assert '"Upload & validate"' in screen
    assert "app.include_router(datasets_router)" in app
    assert "app.include_router(mounted_datasets_router)" in app
