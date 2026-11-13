"""Human-readable Supervisely names with stable Nightjar ownership markers.

The readable portion is shown first in Supervisely. Exact event/dataset/job
identifiers stay at the end so uploads can recover after uncertain API writes
and resets can distinguish Nightjar-owned projects from unrelated work.
"""
import re
from pathlib import PurePosixPath

from .models import CompetitionTeam, Event, LabelingJob, MediaDataset

LEGACY_PROJECT_PREFIX = "Nightjar-"


def display(value: str, limit: int = 56) -> str:
    """Strip control characters and collapse whitespace without losing species names."""
    cleaned = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value or "").split())
    return (cleaned[:limit].rstrip(" -·") or "Untitled")


def project_name(event: Event, dataset: MediaDataset) -> str:
    return (
        f"Nightjar · {display(dataset.name, 52)} · {display(event.name, 36)}"
        f" [NJ:{event.id}:{dataset.id}]"
    )


def legacy_project_name(dataset: MediaDataset) -> str:
    return f"{LEGACY_PROJECT_PREFIX}{dataset.event_id}-{dataset.id}"


def owned_project_event_id(name: str) -> str | None:
    """Only exact Nightjar identifiers authorize a reset to touch a project."""
    old = re.fullmatch(
        r"Nightjar-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-"
        r"([a-zA-Z0-9_-]{1,60})",
        name,
    )
    if old:
        return old.group(1)
    modern = re.search(
        r" \[NJ:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):"
        r"([a-zA-Z0-9_-]{1,60})\]$",
        name,
    )
    if modern and name.startswith("Nightjar · "):
        return modern.group(1)
    return None


def video_label(video_group: str | None) -> str:
    if not video_group:
        return "Image frames"
    # Preserve meaningful video names without exposing internal directory paths.
    title = PurePosixPath(video_group).name.replace("_", " ")
    return display(title, 52)


def dataset_name(job: LabelingJob, team: CompetitionTeam, video_group: str | None) -> str:
    return (f"{display(team.name, 42)} · {video_label(video_group)}"
            f" [NJ:{job.id}]")


def legacy_dataset_name(job: LabelingJob) -> str:
    return f"Nightjar-{job.id}"


def source_parts_label(parts: list[tuple[str, int | None]]) -> str:
    """Show original video and part for a logical single or mixed-video job."""
    segments: list[tuple[str, int | None, int | None]] = []
    for group, index in parts:
        if segments and segments[-1][0] == group:
            previous = segments[-1]
            segments[-1] = (group, previous[1], index)
        else:
            segments.append((group, index, index))
    labels = []
    for group, start, last in segments:
        part = f" · Part {(start - 1) // 30 + 1}" if start is not None else ""
        span = f" ({start}–{last})" if start is not None and last is not None else ""
        labels.append(f"{video_label(group)}{part}{span}")
    return " + ".join(labels) if labels else "Video frames"


def labeling_job_name(job: LabelingJob, team: CompetitionTeam,
                      video_group: str | None, assignee: str,
                      source_parts: list[tuple[str, int | None]] | None = None) -> str:
    prefix = source_parts_label(source_parts) if source_parts else video_label(video_group)
    # Keep the ID marker stable for remote create/reconcile and retries.
    return (
        f"{display(prefix, 158)} · {display(team.name, 24)}"
        f" · {display(assignee, 24)} [NJ:{job.id[:12]}]"
    )


def numbered_job_name(number: int, *, correction_round: int = 0) -> str:
    title = f"Job {number:03d}"
    if correction_round:
        title += " · Incomplete"
        if correction_round > 1:
            title += f" ({correction_round})"
    return title
