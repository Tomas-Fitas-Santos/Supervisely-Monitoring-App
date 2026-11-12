"""Official closeout allows unequal random-review coverage; scores only reviewed annotations."""

from sqlalchemy import select
from test_dataset_workflows import act, teams

from annotatiathon.closeout_api import frame_review_fairness
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    Person,
    ReviewSample,
    StaffAccount,
)

dataset_ctx = __import__("test_dataset_workflows").ctx


def test_unequal_current_round_frame_reviews_allow_final_closeout(dataset_ctx):
    client, maker, csrf = dataset_ctx
    teams(client, csrf)
    with maker.begin() as db:
        owner = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Official videos",
            upload_kind="folder", root_path="/tmp/official",
            created_by=owner, status="ready",
        )
        db.add(dataset)
        db.flush()
        plan = LabelingPlan(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            jobs_per_team=0, redundancy=1, created_by=owner,
            queue_seed="official-2026", status="running",
        )
        db.add(plan)
        db.flush()
        for index, team in enumerate(db.scalars(select(CompetitionTeam).order_by(
            CompetitionTeam.name,
        ))):
            person = db.scalar(select(Person).where(
                Person.team_id == team.id,
            ).limit(1))
            asset = MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                relative_path=f"bird/frame-{index}.jpg", kind="image",
                size_bytes=1, sha256="0" * 64, morphology="bird",
            )
            db.add(asset)
            db.flush()
            job = LabelingJob(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                plan_id=plan.id, team_id=team.id, sequence=1,
                assignee_person_id=person.id, release_mode="shared_primary",
                media_ids=[asset.id], frame_count=1, video_count=1,
                morphology_counts={"bird": 1}, status="on_review",
                annotation_snapshot_round=0, annotation_snapshot_frames=1,
            )
            db.add(job)
            db.flush()
            db.add(ReviewSample(
                event_id=DEFAULT_EVENT_ID, job_id=job.id, team_id=team.id,
                media_id=asset.id, correction_round=0,
                snapshot={"source_relative_path": asset.relative_path,
                          "sha256": "0" * 64, "selection_reason": "submitted_frame",
                          "annotation": {"objects": []}},
                completed_at=400 if index != 2 else None,
                decision=({"correct_boxes": 1, "correct_keypoints": 0,
                           "errors": {}, "missing_classes": [], "weighted_score": 1}
                          if index != 2 else None),
            ))
    with maker() as db:
        fairness = frame_review_fairness(db, DEFAULT_EVENT_ID)
        assert sorted(x["reviewed_frames"] for x in fairness["teams"]) == [0, 1, 1]
        assert fairness["ready_for_winner"]
        assert fairness["total_reviewed_frames"] == 2
        assert fairness["total_available_frames"] == 3
    successful = act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    })
    assert successful.status_code == 200, successful.text
    export = client.get("/api/v1/closeout/exports/frame_reviews")
    assert export.status_code == 200
    assert "correct_boxes" in export.text
    assert "verdicts_json" in export.text
    assert len(export.text.splitlines()) >= 4
    coverage = successful.json()["report"]["review_fairness"]
    assert coverage["ready_for_winner"]
    assert coverage["total_reviewed_frames"] == 2
    assert sorted(item["reviewed_frames"] for item in coverage["teams"]) == [0, 1, 1]
    assert successful.json()["report"]["labeling"]["jobs_accepted"] == 3
    assert successful.json()["report"]["labeling"]["jobs_needing_action"] == 0
