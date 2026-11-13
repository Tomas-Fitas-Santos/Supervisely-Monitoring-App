"""Infer supplied frame order from filenames; legacy manifests remain optional."""
import re
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MediaAsset, MediaVideo


class FrameDescription(BaseModel):
    file: str = Field(min_length=1, max_length=255)
    ordinal: int = Field(ge=0)
    pts: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("file")
    @classmethod
    def basename_only(cls, value):
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Frame files must be basenames")
        return value


class FrameManifest(BaseModel):
    version: Literal[1]
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    source_frame_count: int = Field(ge=1, le=250000)
    all_frames: bool
    time_base: str | None = None
    frame_rate: str | None = None
    frames: list[FrameDescription] = Field(min_length=1, max_length=250000)

    @field_validator("time_base", "frame_rate")
    @classmethod
    def positive_fraction(cls, value):
        try:
            if value is not None and Fraction(value) <= 0:
                raise ValueError("Timing ratios must be positive")
        except ZeroDivisionError as exc:
            raise ValueError("Timing ratios require a nonzero denominator") from exc
        return value


def supplied_frame_number(stem: str) -> int | None:
    """Accept source frame labels such as ColumbaPalumbus_f000013.jpg.

    Prefer explicitly marked frame suffixes over generic trailing numbers.
    Other filenames are handled by deterministic natural ordering below.
    """
    labeled = re.fullmatch(
        r"(?:.+?[_-])?(?:frame|image|img|f)[_-]?(\d+)",
        stem, re.IGNORECASE,
    )
    if labeled:
        return int(labeled.group(1))
    if stem.isdecimal():
        return int(stem)
    generic = re.fullmatch(r".+?[_-](\d+)", stem)
    return int(generic.group(1)) if generic else None


def natural_frame_key(frame: MediaAsset) -> tuple:
    filename = Path(frame.relative_path).name
    return (tuple((0, int(piece)) if piece.isdecimal() else (1, piece.casefold())
                  for piece in re.split(r"(\d+)", filename)), filename.casefold(), filename)


def index_supplied_frames(db: Session, video: MediaVideo, media_root: Path,
                          manifest_path: Path | None, *, strict: bool,
                          path_for_frame: Callable[[str], Path] | None = None) -> None:
    frames = list(db.scalars(select(MediaAsset).where(MediaAsset.video_id == video.id)))
    descriptions = {}
    declared_total = None
    notes = []
    if manifest_path:
        try:
            if manifest_path.stat().st_size > 64 * 1024**2:
                raise ValueError("Manifest exceeds 64 MiB")
            manifest = FrameManifest.model_validate_json(manifest_path.read_bytes())
            descriptions = {entry.file: entry for entry in manifest.frames}
            if len(descriptions) != len(manifest.frames):
                raise ValueError("Duplicate manifest filenames")
            if set(descriptions) != {Path(frame.relative_path).name for frame in frames}:
                raise ValueError("Manifest files do not match supplied frames")
            if any(entry.pts is not None for entry in manifest.frames) and manifest.time_base is None:
                raise ValueError("PTS values require a time base")
        except (ValueError, OSError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid frames.json for {video.video_group}: {exc}") from exc
        video.extraction_policy = "supplied_manifest"
        video.density = "declared_full" if manifest.all_frames else "unknown"
        video.source_sha256 = manifest.source_sha256.lower() if manifest.source_sha256 else None
        video.source_frame_count = manifest.source_frame_count
        video.time_base = manifest.time_base
        video.frame_rate = manifest.frame_rate
        declared_total = manifest.source_frame_count
        strict = True
        notes.append("Frame density is declared by the supplied manifest.")
        if manifest.source_sha256 is None:
            notes.append("Original video checksum was not supplied.")
    # A folder/ZIP always has a deterministic local order even if it does
    # not contain frames.json. Use source suffixes when unambiguous; otherwise
    # assign 1-based ordinals from natural filename order. No JSON is needed.
    frames.sort(key=natural_frame_key)
    parsed = [supplied_frame_number(Path(frame.relative_path).stem) for frame in frames]
    reliable_numbers = (
        bool(frames)
        and all(number is not None and 0 <= number <= 2**31 - 1 for number in parsed)
        and len(set(parsed)) == len(parsed)
    )
    if manifest_path is not None:
        # Legacy manifests explicitly override inferred ordinals.
        reliable_numbers = True
    if not reliable_numbers:
        notes.append(
            "Frame filenames do not contain unique source ordinals; "
            "source order inferred by natural filename sort."
        )
    else:
        frames.sort(key=lambda frame: (
            descriptions[Path(frame.relative_path).name].ordinal
            if descriptions else supplied_frame_number(Path(frame.relative_path).stem)
        ))
    ordinals = [
        (descriptions[Path(frame.relative_path).name].ordinal
         if descriptions else supplied_frame_number(Path(frame.relative_path).stem))
        if reliable_numbers else index
        for index, frame in enumerate(frames, start=1)
    ]
    if (declared_total is not None
            and (min(ordinals, default=0) < 0
                 or max(ordinals, default=0) > declared_total
                 or (min(ordinals, default=0) == 0
                     and max(ordinals, default=0) >= declared_total))):
        raise HTTPException(
            status_code=422,
            detail=f"Manifest ordinals exceed the declared source frame count in {video.video_group}.",
        )
    if len(set(ordinals)) != len(ordinals):
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate source frame ordinals in {video.video_group}.",
        )
    for frame, ordinal in zip(frames, ordinals):
        description = descriptions.get(Path(frame.relative_path).name)
        frame.frame_index = ordinal
        if strict:
            try:
                with Image.open(path_for_frame(frame.relative_path) if path_for_frame
                                else media_root / frame.relative_path) as image:
                    width, height = image.size
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise HTTPException(status_code=422, detail=f"Unreadable frame {frame.relative_path}.") from exc
            if description and ((description.width is not None and description.width != width)
                                or (description.height is not None and description.height != height)):
                raise HTTPException(status_code=422, detail=f"Frame dimensions disagree with frames.json: {frame.relative_path}.")
            if description and description.sha256 and description.sha256.lower() != frame.sha256:
                raise HTTPException(status_code=422, detail=f"Frame checksum disagrees with frames.json: {frame.relative_path}.")
            frame.frame_width, frame.frame_height = width, height
        if description:
            frame.presentation_timestamp = description.pts

    if not ordinals:
        raise HTTPException(status_code=422, detail="Frame folder is empty.")
    lower, upper = min(ordinals), max(ordinals)
    internal_gaps = upper - lower + 1 - len(ordinals)
    if declared_total is not None:
        video.missing_frame_count = declared_total - len(ordinals)
        video.identity_status = "gaps" if video.missing_frame_count else "complete"
    else:
        # Do not misclassify a clipped source that starts at frame 13 as
        # missing 12 images. Missing *internal* frames do matter.
        video.missing_frame_count = internal_gaps if strict else upper - len(ordinals)
        video.identity_status = "gaps" if internal_gaps else "complete"
        if strict and internal_gaps == 0:
            video.density = "declared_full"
            video.source_frame_count = len(frames)
            notes.append(
                "Uploader selected Every source frame. Consecutive provided "
                "frames are treated as a full-density segment; source completeness "
                "cannot be independently verified without the original video."
            )
        elif strict and internal_gaps:
            video.density = "unknown"
            notes.append(
                f"{internal_gaps} missing source ordinals within the supplied "
                "sequence. Import succeeded, but full-density automatic jobs "
                "are disabled for this video; supply consecutive frames or "
                "import the original video."
            )
        else:
            video.density = "sampled"
            notes.append(
                "Supplied frames were imported as sampled imagery. Their "
                "underlying video frame density cannot be independently verified."
            )
    video.identity_notes = notes
