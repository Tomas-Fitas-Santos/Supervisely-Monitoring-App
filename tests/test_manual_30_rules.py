"""Official manual jobs use discrete 30-frame blocks and automatic incomplete returns."""

from types import SimpleNamespace

import test_dataset_workflows
from sqlalchemy import select
from test_dataset_workflows import act, teams, upload_archive

from annotatiathon.models import (
    LabelingJob,
    MediaAsset,
    NativeJobBinding,
    NativeOperation,
    ReviewSample,
)
from annotatiathon.native_worker import sync_one

dataset_ctx = test_dataset_workflows.ctx


def setup_blocks(http, csrf):
    source = {
        f"birds/video/segment/{frame:04d}.jpg": b"bird frame"
        for frame in range(1, 61)
    }
    dataset = upload_archive(http, csrf, source)
    response = act(http, csrf, "/datasets/" + dataset["id"] + "/finalize")
    assert response.status_code == 200, response.text
    teams(http, csrf)
    choices = http.get(
        "/api/v1/labeling/manual/options", params={"dataset_id": dataset["id"]}
    )
    assert choices.status_code == 200, choices.text
    options = choices.json()
    assert len(options["blocks"]) == 2
    assert [block["block_ordinal"] for block in options["blocks"]] == [1, 2]
    assert all(block["frames"] == 30 and not block["reserved"]
               for block in options["blocks"])
    return dataset["id"], options


def test_manual_jobs_select_distinct_contiguous_video_parts(dataset_ctx):
    http, maker, csrf = dataset_ctx
    dataset_id, options = setup_blocks(http, csrf)
    assignments = [
        {
            "team_id": options["teams"][index]["id"],
            "assignee_person_id": options["teams"][index]["members"][0]["id"],
            "video_group": options["blocks"][index]["video_group"],
            "block_ordinal": index + 1,
        }
        for index in range(2)
    ]
    result = act(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": dataset_id, "dispatch": "hold",
        "assignments": assignments,
    })
    assert result.status_code == 201, result.text
    with maker() as db:
        jobs = [db.get(LabelingJob, job_id) for job_id in result.json()["job_ids"]]
        assert [job.frame_count for job in jobs] == [30, 30]
        assert all(job.video_count == 1 for job in jobs)
        assert not set(jobs[0].media_ids).intersection(jobs[1].media_ids)
        for index, job in enumerate(jobs):
            assets = [db.get(MediaAsset, mid) for mid in job.media_ids]
            assert [asset.frame_index for asset in assets] == list(
                range(index * 30 + 1, index * 30 + 31)
            )
    duplicate = act(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": dataset_id, "dispatch": "hold",
        "assignments": [assignments[0]],
    })
    assert duplicate.status_code == 409


def test_incomplete_manual_30_job_returns_without_review(dataset_ctx):
    http, maker, csrf = dataset_ctx
    dataset_id, options = setup_blocks(http, csrf)
    block = options["blocks"][0]
    member = options["teams"][0]["members"][0]
    created = act(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": dataset_id, "dispatch": "hold",
        "assignments": [{
            "team_id": options["teams"][0]["id"],
            "assignee_person_id": member["id"],
            "video_group": block["video_group"],
            "block_ordinal": 1,
        }],
    })
    assert created.status_code == 201, created.text
    job_id = created.json()["job_ids"][0]
    with maker.begin() as db:
        job = db.get(LabelingJob, job_id)
        job.status = "in_progress"
        job.native_dataset_id = 90
        db.add(NativeJobBinding(
            event_id=job.event_id, job_id=job.id,
            person_id=member["id"], native_job_id=12345,
        ))
    native = SimpleNamespace(
        id=12345, dataset_id=90, status="on_review",
        images_count=30, finished_images_count=29,
    )
    api = SimpleNamespace(labeling_job=SimpleNamespace(
        get_info_by_id=lambda _native_id: native,
    ))
    with maker() as db:
        assert sync_one(db, api, db.get(LabelingJob, job_id)) == "needs_correction"
        job = db.get(LabelingJob, job_id)
        assert job.review_status == "returned_incomplete"
        assert job.correction_round == 1
        assert db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "restart_job",
            NativeOperation.target_id == job_id,
        )).status == "pending"
        assert db.scalar(select(ReviewSample.id).where(
            ReviewSample.job_id == job_id,
        )) is None
