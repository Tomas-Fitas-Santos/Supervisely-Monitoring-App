"""Numeric folder identity and explicit supplied-frame manifests."""
import re
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
    ordinal: int = Field(ge=1)
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
    source_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
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


def index_supplied_frames(db: Session, video: MediaVideo, media_root: Path,
                          manifest_path: Path | None, *, strict: bool) -> None:
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
        video.source_sha256 = manifest.source_sha256.lower()
        video.source_frame_count = manifest.source_frame_count
        video.time_base = manifest.time_base
        video.frame_rate = manifest.frame_rate
        declared_total = manifest.source_frame_count
        strict = True
        notes.append("Original video checksum and density are declared by the supplied manifest.")
    seen = set()
    ambiguous = False
    for frame in frames:
        description = descriptions.get(Path(frame.relative_path).name)
        match = re.fullmatch(r"(?:frame[_-]?)?(\d+)", Path(frame.relative_path).stem, re.IGNORECASE)
        ordinal = description.ordinal if description else (int(match[1]) if match else None)
        if ordinal is None or not 1 <= ordinal <= 2**31 - 1 or ordinal in seen or (declared_total and ordinal > declared_total):
            if strict:
                raise HTTPException(status_code=422, detail=f"Ambiguous frame order in {video.video_group}; supply a valid frames.json.")
            ambiguous = True
            frame.frame_index = None
            continue
        seen.add(ordinal)
        frame.frame_index = ordinal
        if strict:
            try:
                with Image.open(media_root / frame.relative_path) as image:
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
    if ambiguous:
        for frame in frames:
            frame.frame_index = None
        video.identity_status = "unknown"
        video.missing_frame_count = None
        notes.append("Unknown or colliding ordinals require a manifest before block planning.")
    else:
        upper = declared_total or max(seen, default=0)
        video.missing_frame_count = upper - len(seen)
        video.identity_status = "gaps" if video.missing_frame_count else "complete"
    if video.density == "unknown":
        notes.append("Sampling density is unknown; numeric filenames do not prove full-density extraction.")
    video.identity_notes = notes
