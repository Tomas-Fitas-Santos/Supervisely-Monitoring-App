"""Register read-only host-mounted datasets and index them without HTTP file uploads.

The browser selects a subdirectory under the administrator-mounted source root.
Only the native worker opens media and uploads paths directly to Supervisely.
No browser transfer, local copy of supplied frames, or public filesystem browsing.
"""
import logging
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .datasets_api import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    dataset_view,
    extract_video_frames,
    get_dataset,
    safe_relative,
    sha256_file,
)
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    DatasetPreparedQueue,
    MediaAsset,
    MediaDataset,
    MediaVideo,
    MountedSourceFile,
    NativeOperation,
    epoch,
)
from .native_media_pool import enqueue_mirror

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


class MountedDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=2, max_length=200)
    extraction_policy: str = Field(pattern="^(all_frames|sample_1fps)$")


def mounted_source(folder: str) -> Path:
    """Only a direct, non-symlink child of the configured read-only mount."""
    name = safe_relative(folder)
    if len(name.parts) != 1:
        raise HTTPException(422, detail="Select one dataset folder under the mounted source directory.")
    base = Path(get_settings().nightjar_dataset_source_dir).resolve()
    if not base.is_dir():
        raise HTTPException(409, detail="Dataset source directory is not mounted on the application host.")
    candidate = base / str(name)
    if candidate.is_symlink() or not candidate.is_dir() or not candidate.resolve().is_relative_to(base):
        raise HTTPException(422, detail="Selected dataset folder is unavailable or unsafe.")
    return candidate


def media_file(ds: MediaDataset, relative: str) -> Path:
    """Validated image path; mounted frames are read straight from the source volume."""
    rel = safe_relative(relative)
    local_root = (Path(ds.root_path) / "media").resolve()
    local = (local_root / str(rel)).resolve()
    if local.is_relative_to(local_root) and local.is_file():
        return local
    if ds.upload_kind == "mounted" and ds.source_folder:
        parts = rel.parts
        if len(parts) < 3 or parts[0] not in {"videos", "images"}:
            raise FileNotFoundError("invalid_mounted_media_path")
        source = mounted_source(ds.source_folder).resolve()
        # The species-first source layout omits the internal 'videos' prefix.
        actual = source.joinpath(*parts[1:])
        if (actual.is_symlink() or not actual.resolve().is_relative_to(source)
                or not actual.is_file()):
            raise FileNotFoundError("mounted_media_unavailable")
        return actual.resolve()
    raise FileNotFoundError("dataset_media_unavailable")


@router.get("/mounted-folders")
def list_mounted_folders(
    _ctx: AuthContext = Depends(requires("organiser")),
):
    root = Path(get_settings().nightjar_dataset_source_dir).resolve()
    if not root.is_dir():
        return {"mounted": False, "folders": []}
    return {"mounted": True, "folders": sorted(
        entry.name for entry in root.iterdir()
        if entry.is_dir() and not entry.is_symlink()
        and not entry.name.startswith(".") and len(entry.name) <= 200
    )}


@router.post("/mounted", status_code=202)
def register_mounted_dataset(
    payload: MountedDatasetRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    source = mounted_source(payload.folder)
    if not any(source.iterdir()):
        raise HTTPException(422, detail="Selected dataset folder is empty.")
    event = current_event(db, ctx, writable=True)
    duplicate = db.scalar(select(MediaDataset).where(
        MediaDataset.event_id == event.id, MediaDataset.upload_kind == "mounted",
        MediaDataset.source_folder == payload.folder,
        MediaDataset.status.in_(("indexing", "ready")),
    ))
    if duplicate:
        raise HTTPException(409, detail="This folder is already registered for the event.")
    ds = MediaDataset(
        event_id=event.id, name=payload.name.strip(), upload_kind="mounted",
        status="indexing", source_folder=payload.folder,
        extraction_policy=payload.extraction_policy,
        root_path="", created_by=ctx.staff.id, last_activity_at=epoch(),
    )
    db.add(ds)
    db.flush()
    directory = Path(get_settings().local_data_dir).resolve() / event.id / ds.id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "media").mkdir()
    ds.root_path = str(directory)
    op = NativeOperation(
        event_id=event.id, kind="index_mounted_dataset", target_id=ds.id,
        status="pending", priority=200,
    )
    db.add(op)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="dataset.mounted.register",
        target_type="media_dataset", target_id=ds.id, details={"folder": payload.folder},
    ))
    db.commit()
    return dataset_view(ds)


@router.post("/{dataset_id}/mounted/retry", status_code=202)
def retry_mounted_index(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.upload_kind != "mounted" or ds.status != "failed":
        raise HTTPException(409, detail="Only failed mounted-folder indexing can be retried.")
    mounted_source(ds.source_folder)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "index_mounted_dataset",
        NativeOperation.target_id == ds.id,
    ))
    if op is None or op.status not in {"attention", "pending"}:
        raise HTTPException(409, detail="Index operation cannot be retried in its current state.")
    op.status = "pending"
    op.error_code = None
    op.claimed_at = None
    ds.status = "indexing"
    ds.error_code = None
    db.commit()
    return dataset_view(ds)


@router.get("/{dataset_id}/mounted/status")
def mounted_index_status(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "index_mounted_dataset",
        NativeOperation.target_id == ds.id,
    ))
    return {"state": op.status if op else "not_applicable",
            "error_code": op.error_code if op else None,
            "attempts": op.attempts if op else 0,
            "pending_seconds": max(0, epoch() - op.created_at)
                if op and op.status == "pending" else None,
            "started_seconds_ago": max(0, epoch() - op.claimed_at)
                if op and op.status == "running" and op.claimed_at else None}


def index_mounted_dataset(db: Session, ds: MediaDataset, progress=None, *, additions=False):
    """Index file metadata in one transaction; supplied image bytes remain mounted."""
    source = mounted_source(ds.source_folder)
    root = Path(ds.root_path)
    media_root = root / "media"
    if not additions and any(media_root.iterdir()):
        raise ValueError("mounted_index_requires_clean_media_directory")
    source_bytes = ds.source_bytes if additions else 0
    frame_count = ds.frame_count if additions else 0
    image_count = ds.image_count if additions else 0
    video_count = ds.video_count if additions else 0
    before = (frame_count, image_count, video_count)
    existing_videos = {v.video_group: v for v in db.scalars(select(MediaVideo).where(
        MediaVideo.dataset_id == ds.id))} if additions else {}
    existing_assets = {rel: (digest, size) for rel, digest, size in db.execute(select(
        MediaAsset.relative_path, MediaAsset.sha256, MediaAsset.size_bytes,
    ).where(MediaAsset.dataset_id == ds.id))} if additions else {}
    receipts = {rel: (size, mtime, digest) for rel, size, mtime, digest in db.execute(select(
        MountedSourceFile.relative_path, MountedSourceFile.size_bytes,
        MountedSourceFile.mtime_ns, MountedSourceFile.sha256,
    ).where(MountedSourceFile.dataset_id == ds.id))} if additions else {}
    seen = set()
    checked = {}
    checked_directories = {source: source.stat().st_mtime_ns}
    receipt_rows = []
    asset_rows = []
    settings = get_settings()
    from .frame_extraction import extract_full_video
    from .frame_import import index_supplied_frames

    def source_stat(path):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError("unsafe_source_file")
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    def save_receipt(path, digest, stat):
        relative = path.relative_to(source).as_posix()
        receipt_rows.append({"dataset_id": ds.id, "relative_path": relative,
            "sha256": digest, "size_bytes": stat[0], "mtime_ns": stat[1]})
        if len(receipt_rows) >= 500:
            db.execute(insert(MountedSourceFile), receipt_rows)
            receipt_rows.clear()

    def known_file(path, expected=None):
        relative = path.relative_to(source).as_posix()
        seen.add(relative)
        stat = source_stat(path)
        previous = receipts.get(relative)
        expected_digest = previous[2] if previous else expected
        if previous and stat == previous[:2]:
            return
        digest, size = sha256_file(path)
        if source_stat(path) != stat:
            raise ValueError(f"source_file_changed_during_sync:{relative}")
        if expected_digest is not None and digest != expected_digest:
            raise ValueError(f"existing_source_file_changed:{relative}")
        if previous:
            db.execute(update(MountedSourceFile).where(
                MountedSourceFile.dataset_id == ds.id,
                MountedSourceFile.relative_path == relative,
            ).values(size_bytes=size, mtime_ns=stat[1]))
        else:
            # Bootstrap pre-receipt imports using their committed media hashes.
            save_receipt(path, digest, stat)

    def count_file(path: Path) -> tuple[str, int]:
        nonlocal source_bytes
        relative = path.relative_to(source).as_posix()
        stat = source_stat(path)
        digest, size = sha256_file(path)
        if source_stat(path) != stat:
            raise ValueError(f"source_file_changed_during_sync:{relative}")
        checked[relative] = stat
        save_receipt(path, digest, stat)
        source_bytes += size
        if source_bytes > settings.max_dataset_bytes:
            raise ValueError("mounted_dataset_exceeds_source_size_limit")
        return digest, size

    def add_asset(path: Path, rel: str, species: str, video: MediaVideo | None,
                  ordinal: int | None = None, pts: int | None = None,
                  width: int | None = None, height: int | None = None):
        nonlocal frame_count, image_count
        if video is not None:
            frame_count += 1
            video.frame_count += 1
            kind = "video_frame"
        else:
            image_count += 1
            kind = "image"
        if frame_count + image_count > settings.max_dataset_assets:
            raise ValueError("mounted_dataset_exceeds_frame_limit")
        digest, size = (sha256_file(path) if path.is_relative_to(media_root)
                        else count_file(path))
        if video is not None:
            video.extracted_bytes += size
        asset_rows.append({
            "event_id": ds.event_id, "dataset_id": ds.id, "relative_path": rel,
            "morphology": species, "kind": kind,
            "video_group": video.video_group if video else None,
            "video_id": video.id if video else None, "frame_index": ordinal,
            "presentation_timestamp": pts, "frame_width": width, "frame_height": height,
            "size_bytes": size, "sha256": digest,
        })
        if (frame_count + image_count) % 500 == 0:
            db.execute(insert(MediaAsset), asset_rows)
            asset_rows.clear()
        if progress:
            progress(phase="indexing", files=frame_count + image_count,
                     source_bytes=source_bytes)

    for species_dir in sorted(source.iterdir()):
        if species_dir.name.startswith("."):
            continue
        if species_dir.is_symlink() or not species_dir.is_dir():
            raise ValueError("dataset_must_contain_species_directories")
        species = str(safe_relative(species_dir.name))
        checked_directories[species_dir] = species_dir.stat().st_mtime_ns
        if "/" in species:
            raise ValueError("invalid_species_folder")
        for entry in sorted(species_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                raise ValueError("source_symlinks_not_allowed")
            if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
                group = f"{species}/{entry.stem}"
                if group in existing_videos:
                    original = existing_videos[group]
                    if not original.source_sha256 or original.extraction_policy.startswith("supplied"):
                        raise ValueError(f"existing_video_layout_changed:{group}")
                    known_file(entry, original.source_sha256)
                    continue
                name = str(safe_relative(entry.name))
                video = MediaVideo(
                    event_id=ds.event_id, dataset_id=ds.id, video_group=group,
                    extraction_policy=ds.extraction_policy, identity_status="complete",
                )
                db.add(video)
                db.flush()
                digest, _size = count_file(entry)
                video.source_sha256 = digest
                video.identity_notes = ["Original video is read-only on the mounted source volume."]
                dest = media_root / "videos" / species / entry.stem
                if additions and dest.exists():
                    # Only this NEW group's uncommitted decoder output can remain after a crash.
                    import shutil
                    if dest.is_symlink() or not dest.resolve().is_relative_to(media_root.resolve()):
                        raise ValueError("unsafe_new_video_output")
                    shutil.rmtree(dest)
                if ds.extraction_policy == "all_frames":
                    result = extract_full_video(entry, dest,
                        max_frames=settings.max_dataset_assets - frame_count - image_count)
                    video.density = "full"
                    video.time_base = result.time_base
                    video.frame_rate = result.frame_rate
                    video.source_frame_count = len(result.frames)
                    for frame in result.frames:
                        add_asset(frame.path, frame.path.relative_to(media_root).as_posix(),
                                  species, video, frame.ordinal, frame.pts,
                                  frame.width, frame.height)
                else:
                    video.density = "sampled"
                    video.identity_status = "unknown"
                    video.missing_frame_count = None
                    for frame in extract_video_frames(entry, dest):
                        add_asset(frame, frame.relative_to(media_root).as_posix(),
                                  species, video)
                video_count += 1
            elif entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                name = str(safe_relative(entry.name))
                relative = str(PurePosixPath("images", species, name))
                if relative in existing_assets:
                    known_file(entry, existing_assets[relative][0])
                    continue
                add_asset(entry, relative,
                          species, None)
            elif entry.is_dir():
                checked_directories[entry] = entry.stat().st_mtime_ns
                title = str(safe_relative(entry.name))
                group = f"{species}/{title}"
                if group in existing_videos:
                    if not existing_videos[group].extraction_policy.startswith("supplied"):
                        raise ValueError(f"existing_video_layout_changed:{group}")
                    for frame in sorted(entry.iterdir()):
                        if frame.name.startswith("."):
                            continue
                        relative = str(PurePosixPath("videos", species, title, frame.name))
                        if relative in existing_assets:
                            known_file(frame, existing_assets[relative][0])
                        elif frame.name == "frames.json":
                            known_file(frame)
                        elif frame.suffix.lower() in IMAGE_EXTENSIONS or frame.is_symlink():
                            raise ValueError(f"existing_video_has_new_frames_use_new_video_folder:{group}")
                    continue
                video = MediaVideo(
                    event_id=ds.event_id, dataset_id=ds.id,
                    video_group=f"{species}/{title}",
                    extraction_policy="supplied_unknown",
                )
                db.add(video)
                db.flush()
                manifest = None
                frames = 0
                for frame in sorted(entry.iterdir()):
                    if frame.is_symlink():
                        raise ValueError("source_symlinks_not_allowed")
                    if frame.name == "frames.json":
                        manifest = frame
                        count_file(frame)
                        continue
                    if frame.suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    name = str(safe_relative(frame.name))
                    relative = str(PurePosixPath("videos", species, title, name))
                    add_asset(frame, relative, species, video)
                    frames += 1
                if not frames:
                    raise ValueError(f"empty_source_video_folder:{video.video_group}")
                if asset_rows:
                    db.execute(insert(MediaAsset), asset_rows)
                    asset_rows.clear()
                db.flush()
                index_supplied_frames(db, video, media_root, manifest,
                    strict=ds.extraction_policy == "all_frames",
                    path_for_frame=lambda rel: source.joinpath(*PurePosixPath(rel).parts[1:]), progress=progress)
                video_count += 1
            elif entry.is_file():
                raise ValueError("expected_species_video_file_or_frame_directory")
    if frame_count + image_count == 0:
        raise ValueError("mounted_source_has_no_usable_frames")
    if asset_rows:
        db.execute(insert(MediaAsset), asset_rows)
    if receipt_rows:
        db.execute(insert(MountedSourceFile), receipt_rows)
    if additions:
        missing = set(receipts) - seen
        # Older datasets without receipts still retain their original source identities.
        missing.update(rel.split("/", 1)[1] for rel in existing_assets
                       if rel.startswith(("videos/", "images/"))
                       and (rel.split("/", 1)[1] not in seen)
                       and not (media_root / rel).is_file())
        if missing:
            raise ValueError(f"existing_source_file_missing:{min(missing)}")
    for relative, stat in checked.items():
        if source_stat(source / relative) != stat:
            raise ValueError(f"source_file_changed_during_sync:{relative}")
    if any(path.stat().st_mtime_ns != modified for path, modified in checked_directories.items()):
        raise ValueError("source_folder_changed_during_sync_finish_copying_then_retry")
    ds.source_bytes = source_bytes
    ds.frame_count = frame_count
    ds.image_count = image_count
    ds.video_count = video_count
    from .dataset_indexing import require_active_import_event
    require_active_import_event(db, ds)
    ds.status = "ready"
    ds.completed_at = epoch()
    ds.error_code = None
    changed = before != (frame_count, image_count, video_count)
    if additions and changed:
        ds.inventory_revision += 1
        prepared = db.get(DatasetPreparedQueue, ds.id)
        if prepared:
            prepared.state = "stale"
    if not additions or changed:
        enqueue_mirror(db, ds)
    return {"new_frames": frame_count - before[0], "new_images": image_count - before[1],
            "new_videos": video_count - before[2], "existing_files": len(seen),
            "inventory_revision": ds.inventory_revision}


def process_mounted_index_once(db: Session) -> dict | None:
    """Local imports share a crash-safe claim/recovery implementation."""
    from .dataset_indexing import process_index_once
    return process_index_once(db, "index_mounted_dataset")

def run_indexer(*, once: bool = False, poll_seconds: int = 8) -> None:
    """Index mounted source folders independently of Supervisely credentials.

    The separate lightweight Docker service runs this loop during ordinary
    Nightjar deployment. Supervisely mirroring stays a distinct native-worker
    operation. A DB row lock ensures the legacy native worker can safely race
    this indexer without claiming the same pending job.
    """
    import time

    from .db import session_factory

    if poll_seconds < 1:
        raise ValueError("poll_seconds_must_be_positive")
    while True:
        try:
            with session_factory()() as db:
                # Preserve mounted indexing as the first local-only operation.
                # An open index requires finishing before queue preparation.
                result = process_mounted_index_once(db)
                if result is None:
                    from .mounted_sync import process_sync_once
                    result = process_sync_once(db)
                if result is None:
                    from .dataset_indexing import process_index_once
                    result = process_index_once(db)
                if result is None:
                    from .dataset_append import process_append_once
                    result = process_append_once(db)
                if result is None:
                    from .balanced_queue import process_preparation_once
                    result = process_preparation_once(db)
            if result is not None:
                logger.info(
                    "Mounted indexing job %s ended with %s",
                    result.get("dataset_id", result.get("operation_id")),
                    result["status"],
                )
        except Exception:
            logger.exception("Mounted indexer cycle failed; will retry")
            result = None
        if once:
            return
        if result is None:
            time.sleep(poll_seconds)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Nightjar local dataset indexer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=8)
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_indexer(once=options.once, poll_seconds=options.poll_seconds)


if __name__ == "__main__":
    main()
