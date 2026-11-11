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
        r" \[NJ:([a-zA-Z0-9_-]{1,60}):"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]$",
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


def labeling_job_name(job: LabelingJob, team: CompetitionTeam,
                      video_group: str | None, assignee: str) -> str:
    return (
        f"{display(team.name, 42)} · {video_label(video_group)}"
        f" · {display(assignee, 44)} · #{job.id[:8]}"
    )
