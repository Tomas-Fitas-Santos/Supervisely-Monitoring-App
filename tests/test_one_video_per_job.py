"""One video is an indivisible, exclusive Nightjar team assignment."""
import pytest
from fastapi import HTTPException

from annotatiathon.labeling_api import MediaUnit, allocate


def video(name: str, frames: int) -> MediaUnit:
    return MediaUnit(
        id="v:" + name, morphology="raptors", video_group=name,
        media_ids=[f"{name}-{i}" for i in range(frames)],
    )


def test_video_only_plan_never_combines_two_videos():
    units = {"raptors": [video("owl-A", 3), video("owl-B", 4)]}
    result, totals = allocate(units, ["team-1"], jobs_per_team=2,
                             redundancy=1, video_limit=None)
    assert totals["job_count"] == 2
    assert {tuple(job.video_groups) for job in result["team-1"]} == {
        ("owl-A",), ("owl-B",)
    }
    assert sorted(job.frame_count for job in result["team-1"]) == [3, 4]


def test_video_plan_does_not_hide_second_video_in_same_job():
    units = {"raptors": [video("owl-A", 3), video("owl-B", 4)]}
    with pytest.raises(HTTPException) as error:
        allocate(units, ["team-1"], jobs_per_team=1,
                 redundancy=1, video_limit=None)
    assert error.value.status_code == 422
    assert "One video per job" in error.value.detail


def test_video_redundancy_remains_per_team_not_per_person():
    units = {"raptors": [video("owl-A", 3)]}
    result, totals = allocate(units, ["team-1", "team-2"],
                             jobs_per_team=1, redundancy=2, video_limit=None)
    assert totals["assignment_copies"] == 6
    assert len(result["team-1"]) == len(result["team-2"]) == 1
    assert result["team-1"][0].media_ids == result["team-2"][0].media_ids
