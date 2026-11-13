"""Official closeout allows unequal random-review coverage; scores only reviewed annotations."""

from sqlalchemy import select
from test_dataset_workflows import act, teams

from annotatiathon.closeout_api import finish_pending_closeouts, frame_review_fairness
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    NativeOperation,
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
    # Only committed validation tasks are admitted at the cutoff.
    with maker.begin() as db:
        submitted = db.scalar(select(LabelingJob).where(
            LabelingJob.event_id == DEFAULT_EVENT_ID,
        ).order_by(LabelingJob.team_id))
        unsynced = LabelingJob(
            event_id=DEFAULT_EVENT_ID, dataset_id=submitted.dataset_id,
            plan_id=submitted.plan_id, team_id=submitted.team_id,
            assignee_person_id=submitted.assignee_person_id, sequence=2,
            release_mode="shared_primary", media_ids=list(submitted.media_ids),
            frame_count=1, video_count=1, morphology_counts={"bird": 1},
            status="on_review",
        )
        db.add(unsynced)
        db.flush()
        operation = NativeOperation(
            event_id=DEFAULT_EVENT_ID, kind="validate_submission",
            target_id=unsynced.id, status="pending",
        )
        db.add(operation)
        db.flush()
        operation_id = operation.id
    started = act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    })
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "finalizing"
    assert started.json()["closeout"]["pending"] == 1
    repeated = act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    })
    assert repeated.json() == started.json()
    with maker() as db:
        finish_pending_closeouts(db)
    assert client.get("/api/v1/closeout/report").json()["event_status"] == "finalizing"
    with maker.begin() as db:
        db.get(NativeOperation, operation_id).status = "attention"
    progress = client.get("/api/v1/closeout/report").json()["closeout"]
    assert progress["issues"][0]["operation_id"] == operation_id
    with maker.begin() as db:
        db.get(NativeOperation, operation_id).status = "done"
    with maker() as db:
        finish_pending_closeouts(db)
    frozen = client.get("/api/v1/closeout/report").json()
    assert frozen["event_status"] == "archived"
    assert frozen["rankings"] == client.get("/api/v1/rankings/teams").json()
    with maker.begin() as db:
        db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
        )).name = "Changed after the freeze"
    assert client.get("/api/v1/rankings/teams").json() == frozen["rankings"]
    export = client.get("/api/v1/closeout/exports/frame_reviews")
    assert export.status_code == 200
    assert "correct_boxes" in export.text
    assert "verdicts_json" in export.text
    assert len(export.text.splitlines()) >= 4
    coverage = frozen["review_fairness"]
    assert coverage["ready_for_winner"]
    assert coverage["total_reviewed_frames"] == 2
    assert sorted(item["reviewed_frames"] for item in coverage["teams"]) == [0, 1, 1]
    assert frozen["labeling"]["jobs_accepted"] == 3
    assert frozen["labeling"]["jobs_needing_action"] == 1
