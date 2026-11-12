"""Official automatic queue carries video tails into the next source video."""
from sqlalchemy import select

from test_dataset_workflows import act, teams

from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MediaVideo,
    Person,
    StaffAccount,
    WorkBlock,
    WorkPacket,
)
from annotatiathon.native_worker import job_video_group
from annotatiathon.work_queue import claim_primary_block

dataset_ctx = __import__("test_dataset_workflows").ctx


def test_automatic_30_frame_stream_crosses_video_boundary_and_keeps_final_tail(dataset_ctx):
    http, maker, csrf = dataset_ctx
    teams(http, csrf)
    with maker.begin() as db:
        owner_id = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Official consecutive videos",
            upload_kind="folder", root_path="/tmp/official_mixed",
            created_by=owner_id, status="ready",
            extraction_policy="all_frames", frame_count=126, video_count=2,
        )
        db.add(dataset)
        db.flush()
        for label, count in (("A", 74), ("B", 52)):
            group = f"Species{label}/video{label}"
            video = MediaVideo(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                video_group=group, extraction_policy="all_frames",
                density="full", identity_status="complete",
                source_frame_count=count, frame_count=count,
            )
            db.add(video)
            db.flush()
            db.add_all(MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                video_id=video.id, video_group=group,
                frame_index=index, morphology=label, kind="video_frame",
                relative_path=f"videos/{group}/{index:05d}.jpg",
                size_bytes=1, sha256="0" * 64,
            ) for index in range(1, count + 1))
        dataset_id = dataset.id

    result = act(http, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert result.status_code == 201, result.text
    summary = result.json()
    assert summary["primary_blocks"] == 5
    assert summary["unique_frames"] == 126
    assert summary["mixed_video_blocks"] == 1
    assert summary["short_tails"] == 1
    assert summary["short_final_frames"] == 6
    assert summary["repeat_frames"] == 120

    with maker() as db:
        blocks = list(db.scalars(select(WorkBlock).where(
            WorkBlock.plan_id == summary["plan_id"],
        ).order_by(WorkBlock.ordinal)))
        lookup = {asset.id: (asset.morphology, asset.frame_index) for asset in
                  db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dataset_id))}
        actual = [[lookup[mid] for mid in block.media_ids] for block in blocks]
        expected = (
            [[("A", i) for i in range(1, 31)],
             [("A", i) for i in range(31, 61)],
             [("A", i) for i in range(61, 75)] + [("B", i) for i in range(1, 17)],
             [("B", i) for i in range(17, 47)],
             [("B", i) for i in range(47, 53)]]
        )
        assert actual == expected
        assert [block.frame_count for block in blocks] == [30, 30, 30, 30, 6]
        assert len({frame for block in blocks for frame in block.media_ids}) == 126
        assert blocks[-1].repeat_media_ids == []

    with maker.begin() as db:
        plan = db.get(LabelingPlan, summary["plan_id"])
        plan.status = "running"
        team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
        ).order_by(CompetitionTeam.name))
        person = db.scalar(select(Person).where(Person.team_id == team.id))
        person.membership_status = "joined"
        person.supervisely_user_id = 987654
        person.supervisely_role = "annotator"
        person.native_verified_at = 1
        team_id, person_id = team.id, person.id

    jobs = []
    for index in range(5):
        with maker.begin() as db:
            job = claim_primary_block(db, summary["plan_id"], team_id, person_id)
            assert job is not None
            assert job.release_mode == "shared_primary"
            assert job.media_ids == blocks[index].media_ids
            assert job.frame_count == len(job.media_ids)
            jobs.append(job.id)
            # A complete native submission frees this annotator for the next
            # independent job. Do not contact Supervisely during this test.
            db.get(WorkPacket, job.id).submitted_at = index + 1
    with maker.begin() as db:
        assert claim_primary_block(db, summary["plan_id"], team_id, person_id) is None

    with maker() as db:
        published = [db.get(LabelingJob, job_id) for job_id in jobs]
        assert [job.video_count for job in published] == [1, 1, 2, 1, 1]
        assert published[2].morphology_counts == {"A": 14, "B": 16}
        assert published[4].morphology_counts == {"B": 6}
        assert job_video_group(db, published[2]) == "videoA + videoB"
        media = http.get(f"/api/v1/labeling/jobs/{published[2].id}/media")
        assert media.status_code == 200, media.text
        assert [(item["morphology"], item["frame_index"]) for item in
                media.json()["items"]] == actual[2]


def test_exact_multiple_of_30_has_no_short_final_block(dataset_ctx):
    http, maker, csrf = dataset_ctx
    teams(http, csrf)
    with maker.begin() as db:
        owner_id = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Two short source videos",
            upload_kind="folder", root_path="/tmp/official_exact",
            created_by=owner_id, status="ready", extraction_policy="all_frames",
            frame_count=60, video_count=2,
        )
        db.add(dataset)
        db.flush()
        for label, count in (("A", 10), ("B", 50)):
            group = f"Species{label}/video{label}"
            video = MediaVideo(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                video_group=group, extraction_policy="all_frames",
                density="full", identity_status="complete",
                source_frame_count=count, frame_count=count,
            )
            db.add(video)
            db.flush()
            db.add_all(MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                video_id=video.id, video_group=group, frame_index=i,
                morphology=label, kind="video_frame",
                relative_path=f"videos/{group}/{i:05d}.jpg",
                size_bytes=1, sha256="0" * 64,
            ) for i in range(1, count + 1))
        dataset_id = dataset.id

    result = act(http, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert result.status_code == 201, result.text
    assert result.json()["primary_blocks"] == 2
    assert result.json()["short_tails"] == 0
    assert result.json()["mixed_video_blocks"] == 1
    with maker() as db:
        blocks = list(db.scalars(select(WorkBlock).where(
            WorkBlock.plan_id == result.json()["plan_id"],
        ).order_by(WorkBlock.ordinal)))
        assert [block.frame_count for block in blocks] == [30, 30]
        assert all(len(block.repeat_media_ids) == 30 for block in blocks)
