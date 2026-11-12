"""Large local dataset ingest: resumable chunked ZIP or folder, safe media inventory.

Browser → chunk endpoint → private disk staging → validated immutable dataset.
The import never uploads to or modifies Supervisely until separately approved.
"""
import hashlib
import shutil
import subprocess
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .config import get_settings
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    DatasetStagedFile,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MediaVideo,
    NativeOperation,
    epoch,
)
from .native_media_pool import enqueue_mirror, mirror_status

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])
VIEWERS = ("organiser", "monitor", "observer", "reception", "activity", "distribution")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpeg", ".mpg"}
VIDEO_FRAMES_PER_SECOND = 1  # Documented default for raw video imports.
MAX_CHUNK_BYTES = 16 * 1024 * 1024
STALE_UPLOAD_SECONDS = 300  # A browser cannot hold the source File after reload.


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    upload_kind: str = Field(pattern="^(zip|folder)$")
    extraction_policy: Literal["sample_1fps", "all_frames"] = "sample_1fps"


def dataset_view(ds: MediaDataset) -> dict:
    # Display old/orphaned imports as interrupted even if the browser disappeared
    # before it could explicitly stop the transfer. Never mutate in a GET.
    status = ds.status
    if status == "uploading" and epoch() - ds.last_activity_at >= STALE_UPLOAD_SECONDS:
        status = "interrupted"
    return {
        "id": ds.id, "name": ds.name, "event_id": ds.event_id,
        "upload_kind": ds.upload_kind, "source_folder": ds.source_folder,
        "status": status,
        "extraction_policy": ds.extraction_policy,
        "source_bytes": ds.source_bytes, "image_count": ds.image_count,
        "frame_count": ds.frame_count, "video_count": ds.video_count,
        "native_project_id": ds.native_project_id,
        "native_dataset_id": ds.native_dataset_id,
        "created_at": ds.created_at, "completed_at": ds.completed_at,
        "last_activity_at": ds.last_activity_at,
        "error_code": ds.error_code,
    }


def get_dataset(db: Session, event_id: str, dsid: str,
                *, lock: bool = False) -> MediaDataset:
    query = select(MediaDataset).where(
        MediaDataset.id == dsid, MediaDataset.event_id == event_id,
    )
    if lock:
        query = query.with_for_update()
    ds = db.scalar(query)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found in selected event.")
    return ds


def safe_relative(name: str) -> PurePosixPath:
    if (not name or "\\" in name or "\x00" in name
            or name.startswith("/") or len(name) > 1024 or ":" in name):
        raise HTTPException(status_code=422, detail="Invalid dataset-relative file path.")
    path = PurePosixPath(name)
    if any(part in (".", "..", "") or part.startswith(".") for part in path.parts):
        raise HTTPException(status_code=422, detail="Hidden, unsafe or parent paths not allowed.")
    if len(path.parts) > 8 or any(len(part) > 200 for part in path.parts):
        raise HTTPException(status_code=422, detail="Media path exceeds allowed depth.")
    return path


def root_dir(ds: MediaDataset) -> Path:
    return Path(ds.root_path).resolve(strict=False)


@router.get("")
def list_datasets(
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    items = db.scalars(select(MediaDataset).where(
        MediaDataset.event_id == event.id,
    ).order_by(MediaDataset.created_at.desc())).all()
    return {"items": [dataset_view(ds) for ds in items]}


@router.post("/uploads", status_code=201)
def create_upload(
    payload: DatasetCreate,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    # The local storage directory is configured by the administrator, never by a client.
    root = Path(get_settings().local_data_dir).resolve() / event.id
    ds = MediaDataset(
        event_id=event.id, name=payload.name.strip(), upload_kind=payload.upload_kind,
        extraction_policy=payload.extraction_policy,
        root_path="", created_by=ctx.staff.id, last_activity_at=epoch(),
    )
    db.add(ds)
    db.flush()
    directory = root / ds.id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "stage").mkdir()
    (directory / "media").mkdir()
    ds.root_path = str(directory)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="dataset.create",
        target_type="media_dataset", target_id=ds.id,
        details={"kind": ds.upload_kind, "name": ds.name},
    ))
    db.commit()
    return dataset_view(ds)


@router.get("/{dataset_id}/upload-status")
def upload_status(
    dataset_id: str,
    relative_path: str | None = Query(default=None, max_length=1024),
    include_files: bool = Query(default=False),
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    files = db.scalars(select(DatasetStagedFile).where(
        DatasetStagedFile.dataset_id == ds.id,
    )).all()
    result = {"dataset": dataset_view(ds),
              "files": len(files),
              "received_bytes": sum(f.received_bytes for f in files),
              "complete_files": sum(
                  f.received_bytes == f.expected_bytes for f in files
              )}
    if include_files:
        result["file_progress"] = [{
            "relative_path": f.relative_path,
            "received_bytes": f.received_bytes, "expected_bytes": f.expected_bytes,
        } for f in files]
    if relative_path:
        rel = str(safe_relative(relative_path))
        found = next((f for f in files if f.relative_path == rel), None)
        result["file"] = ({
            "received_bytes": found.received_bytes,
            "expected_bytes": found.expected_bytes,
        } if found else None)
    return result


@router.post("/{dataset_id}/interrupt")
def interrupt_upload(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    """Explicitly stop an incomplete transfer; existing chunks stay resumable."""
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status not in {"uploading", "interrupted"}:
        raise HTTPException(status_code=409, detail="Only unfinished uploads can be interrupted.")
    ds.status = "interrupted"
    ds.error_code = "upload_interrupted"
    db.commit()
    return dataset_view(ds)


@router.post("/{dataset_id}/resume")
def resume_upload(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    """Resume using the original files reselected in the browser."""
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status == "uploading":
        if epoch() - ds.last_activity_at < STALE_UPLOAD_SECONDS:
            raise HTTPException(status_code=409, detail="Upload is still active.")
    elif ds.status != "interrupted":
        raise HTTPException(status_code=409, detail="Dataset cannot be resumed.")
    ds.status = "uploading"
    ds.error_code = None
    ds.last_activity_at = epoch()
    db.commit()
    return dataset_view(ds)


@router.delete("/{dataset_id}")
def delete_incomplete_dataset(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    """Remove orphan upload rows and their staging directory; never touch native media."""
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status == "uploading" and epoch() - ds.last_activity_at < STALE_UPLOAD_SECONDS:
        raise HTTPException(
            status_code=409, detail="Stop the active upload before deleting it."
        )
    if ds.status not in {"uploading", "interrupted", "failed"}:
        raise HTTPException(
            status_code=409, detail="Only incomplete uploads can be deleted."
        )
    if ds.native_project_id is not None or ds.native_dataset_id is not None:
        raise HTTPException(
            status_code=409, detail="Dataset has Supervisely references and cannot be deleted."
        )
    if (db.scalar(select(LabelingPlan.id).where(LabelingPlan.dataset_id == ds.id))
            or db.scalar(select(LabelingJob.id).where(LabelingJob.dataset_id == ds.id))
            or db.scalar(select(MediaAsset.id).where(MediaAsset.dataset_id == ds.id))):
        raise HTTPException(
            status_code=409, detail="Dataset has dependent records and cannot be deleted."
        )
    expected = (Path(get_settings().local_data_dir).resolve() / event.id / ds.id)
    if not ds.root_path or root_dir(ds) != expected or expected.is_symlink():
        raise HTTPException(status_code=409, detail="Unsafe dataset directory; inspect manually.")
    # Failure to delete files leaves the database record intact for a safe retry.
    if expected.exists():
        shutil.rmtree(expected)
    db.execute(delete(DatasetStagedFile).where(DatasetStagedFile.dataset_id == ds.id))
    if ds.upload_kind == "mounted":
        db.execute(delete(NativeOperation).where(
            NativeOperation.kind == "index_mounted_dataset",
            NativeOperation.target_id == ds.id,
            NativeOperation.status == "attention",
        ))
    db.delete(ds)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="dataset.delete_incomplete",
        target_type="media_dataset", target_id=dataset_id,
        details={"name": ds.name, "source_bytes": ds.source_bytes},
    ))
    db.commit()
    return {"deleted": True, "dataset_id": dataset_id}


@router.put("/uploads/{dataset_id}/chunk")
async def receive_chunk(
    dataset_id: str, request: Request,
    relative_path: str = Query(min_length=1, max_length=1024),
    offset: int = Query(ge=0),
    total: int = Query(ge=0),
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status != "uploading":
        raise HTTPException(status_code=409, detail="Dataset is no longer uploading.")
    rel = safe_relative(relative_path)
    if ds.upload_kind == "zip" and str(rel) != "source.zip":
        raise HTTPException(status_code=422, detail="ZIP imports accept source.zip only.")
    if ds.upload_kind == "folder" and len(rel.parts) < 2:
        raise HTTPException(
            status_code=422, detail="Select the dataset root with species/video subfolders."
        )
    settings = get_settings()
    if total > settings.max_dataset_bytes or ds.source_bytes > settings.max_dataset_bytes:
        raise HTTPException(status_code=413, detail="Dataset exceeds configured byte limit.")
    entry = db.scalar(select(DatasetStagedFile).where(
        DatasetStagedFile.dataset_id == ds.id,
        DatasetStagedFile.relative_path == str(rel),
    ).with_for_update())
    if entry is None:
        if offset != 0:
            raise HTTPException(status_code=409, detail="File must start at offset zero.")
        entry = DatasetStagedFile(
            dataset_id=ds.id, relative_path=str(rel), expected_bytes=total,
        )
        db.add(entry)
        db.flush()
    elif total != entry.expected_bytes or offset != entry.received_bytes:
        raise HTTPException(
            status_code=409, detail="Upload offset/size differs from stored progress."
        )
    target = root_dir(ds) / "stage" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise HTTPException(status_code=409, detail="Symlinked staging path not allowed.")
    existing = target.stat().st_size if target.exists() else 0
    if existing != offset:
        raise HTTPException(
            status_code=409, detail="On-disk progress differs from database. Stop and inspect."
        )
    written = 0
    try:
        with target.open("r+b" if target.exists() else "wb") as stream:
            stream.seek(offset)
            async for block in request.stream():
                written += len(block)
                if written > MAX_CHUNK_BYTES or offset + written > total:
                    raise HTTPException(
                        status_code=413, detail="Upload chunk exceeds size or remaining bytes."
                    )
                stream.write(block)
    except Exception:
        # Roll back filesystem progress to match the uncommitted database state.
        if target.exists():
            with target.open("r+b") as stream:
                stream.truncate(offset)
        raise
    entry.received_bytes += written
    ds.source_bytes += written
    ds.last_activity_at = epoch()
    if ds.source_bytes > settings.max_dataset_bytes:
        with target.open("r+b") as stream:
            stream.truncate(offset)
        raise HTTPException(status_code=413, detail="Dataset size limit reached.")
    db.commit()
    return {
        "relative_path": str(rel), "received_bytes": entry.received_bytes,
        "expected_bytes": total, "complete": entry.received_bytes == total,
    }


def canonical_name(raw: str, root_name: str | None) -> str:
    """Normalize species-first uploads to the existing internal video/frame layout.

    Accepted:
      species/video.mp4
      species/video_name/frame.jpg
      dataset_name/species/video.mp4             (with inferred wrapper)
      dataset_name/species/video_name/frame.jpg  (with inferred wrapper)

    Legacy images/ and videos/ collections stay readable for existing tests
    and older uploads. Newly imported videos always become frame assets.
    """
    path = safe_relative(raw)
    if root_name is not None and path.parts and path.parts[0] == root_name:
        path = PurePosixPath(*path.parts[1:])
    parts = path.parts
    extension = path.suffix.lower()
    if not parts:
        raise HTTPException(status_code=422, detail="Dataset folder is empty.")
    if parts[0] == "images":
        if len(parts) != 3 or extension not in IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail="Legacy images require images/<species>/<image file>.",
            )
        return str(path)
    if parts[0] == "videos":
        if ((len(parts) == 4 and (extension in IMAGE_EXTENSIONS or parts[-1] == "frames.json"))
                or (len(parts) == 3 and extension in VIDEO_EXTENSIONS)):
            return str(path)
        raise HTTPException(
            status_code=422,
            detail="Legacy videos require videos/<species>/<video>/<frame> or a video file.",
        )
    if len(parts) == 2 and extension in VIDEO_EXTENSIONS:
        return str(PurePosixPath("videos", *parts))
    if len(parts) == 3 and (extension in IMAGE_EXTENSIONS or parts[-1] == "frames.json"):
        return str(PurePosixPath("videos", *parts))
    raise HTTPException(
        status_code=422,
        detail="Expected <species>/<video-file> or "
               "<species>/<video-name>/<frame-image>; no images/videos folders required.",
    )


def infer_wrapper(paths: list[str]) -> str | None:
    """Detect an optional enclosing dataset directory without guessing species.

    A species-only dataset must not be mistaken for a wrapper. File-extension
    and expected depth distinguish species/video.mp4 and species/video/frame.jpg.
    """
    if not paths:
        raise HTTPException(status_code=422, detail="No video files or frames found.")

    def valid(wrapper: str | None) -> bool:
        try:
            for name in paths:
                canonical_name(name, wrapper)
            return True
        except HTTPException:
            return False

    if valid(None):
        return None
    first = [safe_relative(name).parts[0] for name in paths]
    if len(set(first)) == 1 and valid(first[0]):
        return first[0]
    raise HTTPException(
        status_code=422,
        detail="Dataset must contain species folders with video files or "
               "video-frame subfolders, optionally inside one dataset folder.",
    )


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def extract_video_frames(source: Path, destination: Path) -> list[Path]:
    """Sample raw videos at 1 FPS into immutable JPEG frame files.

    Supervisely labeling jobs are image-frame jobs. Source videos are retained
    separately under the dataset's private source_videos directory.
    """
    if destination.exists():
        raise HTTPException(status_code=422, detail="Duplicate video folder name.")
    destination.mkdir(parents=True)
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vf", f"fps={VIDEO_FRAMES_PER_SECOND}",
        "-q:v", "3", "-start_number", "1",
        str(destination / "frame_%08d.jpg"),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, check=False, timeout=3600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(
            status_code=503,
            detail="Video frame extraction is unavailable or exceeded its time limit.",
        ) from exc
    if result.returncode != 0:
        raise HTTPException(
            status_code=422, detail=f"Could not decode video {source.name}.",
        )
    frames = sorted(destination.glob("frame_*.jpg"))
    if not frames:
        raise HTTPException(
            status_code=422, detail=f"Video {source.name} contains no extractable frames.",
        )
    return frames


def safe_zip_entries(file: zipfile.ZipFile, max_count: int,
                     max_bytes: int) -> list[zipfile.ZipInfo]:
    files = []
    unpacked = 0
    for item in file.infolist():
        if item.is_dir():
            continue
        if len(files) >= max_count:
            raise HTTPException(status_code=413, detail="Dataset has too many files.")
        if (item.flag_bits & 1) or ((item.external_attr >> 16) & 0o170000) == 0o120000:
            raise HTTPException(status_code=422, detail="Encrypted/symlink ZIP entries blocked.")
        safe_relative(item.filename)
        unpacked += item.file_size
        if unpacked > max_bytes or (
            item.file_size > 16_000_000 and
            item.file_size > max(item.compress_size, 1) * 150
        ):
            raise HTTPException(status_code=413, detail="Excessively large ZIP contents.")
        files.append(item)
    return files


def copy_and_hash(source, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as writer:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            writer.write(block)
            digest.update(block)
            written += len(block)
    return digest.hexdigest(), written


def staged_sources(ds: MediaDataset, files: list[DatasetStagedFile]):
    stage = root_dir(ds) / "stage"
    if ds.upload_kind == "zip":
        if len(files) != 1 or files[0].relative_path != "source.zip":
            raise HTTPException(status_code=422, detail="Upload one ZIP archive.")
        archive = stage / "source.zip"
        with zipfile.ZipFile(archive) as zipped:
            entries = safe_zip_entries(
                zipped, get_settings().max_dataset_assets,
                get_settings().max_dataset_bytes,
            )
            names = [x.filename for x in entries]
            wrapper = infer_wrapper(names)
            canonical = [canonical_name(x, wrapper) for x in names]
            if len(set(canonical)) != len(canonical):
                raise HTTPException(status_code=422, detail="Duplicate archive media paths.")
            for item, rel in zip(entries, canonical):
                with zipped.open(item) as stream:
                    yield rel, stream
    else:
        names = [f.relative_path for f in files]
        wrapper = infer_wrapper(names)
        canonical = [canonical_name(n, wrapper) for n in names]
        if len(set(canonical)) != len(canonical):
            raise HTTPException(status_code=422, detail="Duplicate media paths.")
        for f, rel in zip(files, canonical):
            with (stage / f.relative_path).open("rb") as stream:
                yield rel, stream


@router.get("/{dataset_id}/mirror-status")
def dataset_mirror_status(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", "monitor", "observer")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    return mirror_status(db, ds)


@router.post("/{dataset_id}/mirror/retry", status_code=202)
def retry_dataset_mirror(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status != "ready":
        raise HTTPException(status_code=409, detail="Finish local video upload first.")
    op = enqueue_mirror(db, ds)
    if op.status == "done":
        return {"status": "done", "operation_id": op.id}
    if op.status not in {"attention", "pending"}:
        raise HTTPException(status_code=409, detail="Dataset mirror already running.")
    op.status = "pending"
    op.error_code = None
    op.claimed_at = None
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="dataset.mirror.retry", target_type="media_dataset",
        target_id=ds.id, details={"operation_id": op.id},
    ))
    db.commit()
    return {"status": "pending", "operation_id": op.id}


@router.post("/{dataset_id}/finalize")
def finalize_dataset(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status == "ready":
        return dataset_view(ds)
    if ds.status != "uploading":
        raise HTTPException(status_code=409, detail="Dataset cannot be finalized in this state.")
    files = list(db.scalars(select(DatasetStagedFile).where(
        DatasetStagedFile.dataset_id == ds.id,
    ).order_by(DatasetStagedFile.relative_path)))
    if not files or any(f.received_bytes != f.expected_bytes for f in files):
        raise HTTPException(status_code=409, detail="Some upload files are still incomplete.")
    if ds.upload_kind == "folder" and len(files) > get_settings().max_dataset_assets:
        raise HTTPException(status_code=413, detail="Dataset exceeds media file limit.")
    media_dir = root_dir(ds) / "media"
    if any(media_dir.iterdir()):
        raise HTTPException(
            status_code=409, detail="An interrupted dataset finalization needs manual recovery."
        )
    counts: Counter[str] = Counter()
    videos: set[str] = set()
    morph_counts: Counter[str] = Counter()
    assets = []
    source_videos = root_dir(ds) / "source_videos"
    source_group_names: set[str] = set()
    frame_group_names: set[str] = set()
    video_records: dict[str, MediaVideo] = {}
    manifests: dict[str, Path] = {}

    def get_video(group: str, policy: str) -> MediaVideo:
        if group not in video_records:
            video = MediaVideo(event_id=event.id, dataset_id=ds.id,
                               video_group=group, extraction_policy=policy)
            db.add(video)
            db.flush()
            video_records[group] = video
        return video_records[group]

    def register_asset(rel: str, morph: str, group: str | None,
                       kind: str, digest: str, size: int, *, ordinal: int | None = None,
                       pts: int | None = None, width: int | None = None,
                       height: int | None = None) -> None:
        counts[kind] += 1
        morph_counts[morph] += 1
        if group:
            videos.add(group)
        video = video_records.get(group)
        if video:
            video.frame_count += 1
            video.extracted_bytes += size
        assets.append(MediaAsset(
            event_id=event.id, dataset_id=ds.id,
            relative_path=rel, morphology=morph, kind=kind,
            video_group=group, size_bytes=size, sha256=digest,
            video_id=video.id if video else None, frame_index=ordinal,
            presentation_timestamp=pts, frame_width=width, frame_height=height,
        ))
        if sum(counts.values()) > get_settings().max_dataset_assets:
            raise HTTPException(status_code=413, detail="Too many extracted video frames.")
        if len(assets) >= 500:
            db.add_all(assets)
            db.flush()
            assets.clear()

    try:
        for rel, stream in staged_sources(ds, files):
            parts = PurePosixPath(rel).parts
            morph = parts[1]
            extension = PurePosixPath(rel).suffix.lower()
            if extension in VIDEO_EXTENSIONS:
                # A direct species/video.mp4 input becomes a video frame folder.
                name = PurePosixPath(parts[2]).stem
                if name in {"", ".", ".."}:
                    raise HTTPException(status_code=422, detail="Video title must identify a source file.")
                group = f"{morph}/{name}"
                if group in source_group_names or group in frame_group_names:
                    raise HTTPException(status_code=422, detail="Duplicate video title.")
                source_group_names.add(group)
                original = source_videos / morph / parts[2]
                digest, _source_size = copy_and_hash(stream, original)
                video = get_video(group, ds.extraction_policy)
                video.source_sha256 = digest
                video.source_relative_path = original.relative_to(root_dir(ds)).as_posix()
                video.identity_status = "complete"
                video.missing_frame_count = 0
                output = media_dir / "videos" / morph / name
                if ds.extraction_policy == "all_frames":
                    from .frame_extraction import extract_full_video
                    extraction = extract_full_video(
                        original, output,
                        max_frames=get_settings().max_dataset_assets - sum(counts.values()),
                    )
                    video.density = "full"
                    video.time_base = extraction.time_base
                    video.frame_rate = extraction.frame_rate
                    video.source_frame_count = len(extraction.frames)
                    if extraction.time_base is None or any(frame.pts is None for frame in extraction.frames):
                        video.identity_notes = ["Some source presentation timestamps are unavailable; decoded ordinals remain authoritative."]
                    for frame in extraction.frames:
                        digest, size = sha256_file(frame.path)
                        register_asset(frame.path.relative_to(media_dir).as_posix(), morph, group,
                                       "video_frame", digest, size, ordinal=frame.ordinal,
                                       pts=frame.pts, width=frame.width, height=frame.height)
                else:
                    video.density = "sampled"
                    video.identity_status = "unknown"
                    video.missing_frame_count = None
                    video.identity_notes = ["Source ordinals are unavailable for one-frame-per-second sampling."]
                    for frame in extract_video_frames(original, output):
                        digest, size = sha256_file(frame)
                        register_asset(frame.relative_to(media_dir).as_posix(), morph, group,
                                       "video_frame", digest, size)
            else:
                kind = "image" if parts[0] == "images" else "video_frame"
                group = f"{morph}/{parts[2]}" if kind == "video_frame" else None
                if group:
                    if group in source_group_names:
                        raise HTTPException(status_code=422, detail="Duplicate video title.")
                    frame_group_names.add(group)
                    video = get_video(group, "supplied_unknown")
                    if parts[-1] == "frames.json":
                        path = root_dir(ds) / "provenance" / video.id / "frames.json"
                        copy_and_hash(stream, path)
                        manifests[group] = path
                        continue
                digest, size = copy_and_hash(stream, media_dir / rel)
                register_asset(rel, morph, group, kind, digest, size)
        db.add_all(assets)
        db.flush()
        if not counts:
            raise HTTPException(status_code=422, detail="No usable video frames found.")
        from .frame_import import index_supplied_frames
        for group, video in video_records.items():
            if video.extraction_policy == "supplied_unknown":
                index_supplied_frames(db, video, media_dir, manifests.get(group),
                                      strict=ds.extraction_policy == "all_frames")
        ds.image_count = counts["image"]
        ds.frame_count = counts["video_frame"]
        ds.video_count = len(videos)
        ds.status = "ready"
        ds.completed_at = epoch()
        # Preload the complete collection before event launch. The SDK worker
        # handles actual remote writes independently of this HTTP request.
        enqueue_mirror(db, ds)
        db.add(AuditEvent(
            staff_id=ctx.staff.id, event_id=event.id, action="dataset.finalize",
            target_type="media_dataset", target_id=ds.id,
            details={
                "images": ds.image_count, "frames": ds.frame_count,
                "videos": ds.video_count, "morphologies": dict(morph_counts),
            },
        ))
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(media_dir, ignore_errors=True)
        shutil.rmtree(source_videos, ignore_errors=True)
        provenance = root_dir(ds) / "provenance"
        if provenance.resolve().parent == root_dir(ds):
            shutil.rmtree(provenance, ignore_errors=True)
        media_dir.mkdir(exist_ok=True)
        raise
    # Media stays on disk; upload staging no longer needed.
    shutil.rmtree(root_dir(ds) / "stage", ignore_errors=True)
    return dataset_view(ds)


@router.get("/{dataset_id}/assets")
def list_assets(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    morphology: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None, pattern="^(image|video_frame)$"),
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    query = select(MediaAsset).where(MediaAsset.dataset_id == ds.id)
    if morphology:
        query = query.where(MediaAsset.morphology == morphology)
    if kind:
        query = query.where(MediaAsset.kind == kind)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    records = db.scalars(query.order_by(MediaAsset.video_group, MediaAsset.frame_index.nulls_last(),
                                          MediaAsset.relative_path, MediaAsset.id)
                         .limit(size).offset((page-1)*size)).all()
    return {
        "dataset": dataset_view(ds),
        "total": total, "page": page, "size": size,
        "items": [{
            "id": x.id, "relative_path": x.relative_path,
            "morphology": x.morphology, "kind": x.kind,
            "video_group": x.video_group, "size_bytes": x.size_bytes,
            "video_id": x.video_id, "frame_index": x.frame_index,
            "presentation_timestamp": x.presentation_timestamp,
            "frame_width": x.frame_width, "frame_height": x.frame_height,
            "native_image_id": x.native_image_id,
        } for x in records],
    }


@router.get("/{dataset_id}/videos")
def list_videos(
    dataset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    dataset = get_dataset(db, event.id, dataset_id)
    videos = db.scalars(select(MediaVideo).where(
        MediaVideo.dataset_id == dataset.id, MediaVideo.event_id == event.id,
    ).order_by(MediaVideo.video_group, MediaVideo.id))
    return {"items": [{
        "id": video.id, "video_group": video.video_group,
        "extraction_policy": video.extraction_policy, "density": video.density,
        "source_sha256": video.source_sha256, "source_relative_path": video.source_relative_path,
        "frame_count": video.frame_count, "source_frame_count": video.source_frame_count,
        "extracted_bytes": video.extracted_bytes, "time_base": video.time_base,
        "frame_rate": video.frame_rate, "identity_status": video.identity_status,
        "missing_frame_count": video.missing_frame_count, "identity_notes": video.identity_notes,
    } for video in videos]}


@router.get("/{dataset_id}/summary")
def dataset_summary(
    dataset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    counts = db.execute(select(
        MediaAsset.morphology, MediaAsset.kind, func.count(MediaAsset.id),
    ).where(MediaAsset.dataset_id == ds.id).group_by(
        MediaAsset.morphology, MediaAsset.kind
    )).all()
    return {
        "dataset": dataset_view(ds),
        "morphologies": [{
            "name": name, "kind": kind, "count": int(count),
        } for name, kind, count in counts],
        "total_assets": ds.image_count + ds.frame_count,
    }
