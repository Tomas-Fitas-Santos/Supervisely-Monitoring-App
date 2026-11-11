"""Single-pass full-density FFmpeg extraction with presentation-order provenance."""
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    path: Path
    ordinal: int
    pts: int | None
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FullExtraction:
    frames: list[DecodedFrame]
    time_base: str | None
    frame_rate: str | None


def extract_full_video(source: Path, destination: Path, *, max_frames: int = 250000) -> FullExtraction:
    if destination.exists():
        raise HTTPException(status_code=422, detail="Duplicate video folder name.")
    destination.mkdir(parents=True)
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-xerror", "-copyts", "-i", str(source),
        "-map", "0:V:0", "-an", "-sn", "-dn", "-vf", "showinfo",
        "-fps_mode", "passthrough", "-frames:v", str(max_frames + 1),
        "-q:v", "3", "-threads", "2", "-start_number", "1",
        str(destination / "frame_%08d.jpg"),
    ]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log,
                                    check=False, timeout=3600)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail="Full-frame extraction is unavailable or timed out.") from exc
        if result.returncode:
            raise HTTPException(status_code=422, detail=f"Could not decode video {source.name}.")
        files = list(destination.glob("frame_*.jpg"))
        if len(files) > max_frames:
            raise HTTPException(status_code=413, detail="Too many extracted video frames.")
        frames = []
        time_base = None
        frame_rate = None
        log.seek(0)
        for line in log:
            if "showinfo" not in line:
                continue
            configuration = re.search(r"config in time_base:\s*(\d+/\d+), frame_rate:\s*(\d+/\d+)", line)
            if configuration:
                time_base, frame_rate = configuration.groups()
            frame = re.search(r"\bn:\s*(\d+).*?\bpts:\s*(-?\d+|NOPTS).*?\bs:(\d+)x(\d+)", line)
            if frame:
                index, timestamp, width, height = frame.groups()
                index, width, height = int(index), int(width), int(height)
                pts = None if timestamp == "NOPTS" else int(timestamp)
                path = destination / f"frame_{index + 1:08d}.jpg"
                if index != len(frames) or not path.is_file():
                    raise HTTPException(status_code=422, detail="Decoded frame identity is incomplete.")
                frames.append(DecodedFrame(path, index + 1, pts, width, height))
    if not frames or len(frames) != len(files):
        raise HTTPException(status_code=422, detail="Video has no complete presentation-order frame metadata.")
    if time_base and time_base.endswith("/0"):
        time_base = None
    return FullExtraction(frames, time_base, None if frame_rate == "0/0" else frame_rate)
