"""Official object-level score weights and organiser-only rubric updates."""

from sqlalchemy import select
from test_dataset_workflows import act, teams

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
    epoch,
    identifier,
)
from annotatiathon.scoring_api import resolved_weights, score_verdict
from annotatiathon.security import password_hash

dataset_ctx = __import__("test_dataset_workflows").ctx


def test_visible_occluded_box_and_wrong_reason_points_and_admin_gate(dataset_ctx):
    client, maker, csrf = dataset_ctx
    teams(client, csrf)
    with maker.begin() as db:
        organiser = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "owner@example.org",
        ))
        db.add(StaffAccount(
            email="weights-monitor@example.org", name="Weights Monitor",
            roles=["monitor"], password_hash=password_hash("StrongMonitorPassword123!"),
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Scoring source",
            upload_kind="folder", root_path="/tmp/weighted",
            created_by=organiser.id, status="ready",
        )
        db.add(dataset)
        db.flush()
        plan = LabelingPlan(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            jobs_per_team=0, redundancy=1, created_by=organiser.id,
            queue_seed="weighted-score", status="running",
        )
        db.add(plan)
        db.flush()
        team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
        ).order_by(CompetitionTeam.name))
        person = db.scalar(select(Person).where(Person.team_id == team.id))
        asset = MediaAsset(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            relative_path="bird/frame-1.jpg", kind="image",
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
            correction_round=0,
        )
        db.add(job)
        db.flush()
        sample = ReviewSample(
            event_id=DEFAULT_EVENT_ID, job_id=job.id, team_id=team.id,
            media_id=asset.id, correction_round=0,
            reviewer_id=organiser.id,
            claim_token=identifier(), expires_at=epoch() + 1000,
            snapshot={
                "sha256": "0" * 64,
                "source_relative_path": asset.relative_path,
                "selection_reason": "submitted_frame",
                "annotation": {"objects": [
                    {"classTitle": "bird", "geometryType": "rectangle"},
                    {"classTitle": "left_eye", "geometryType": "point"},
                    {"classTitle": "beak_occ", "geometryType": "point"},
                    {"classTitle": "left_eye_occ", "geometryType": "point"},
                    {"classTitle": "right_eye", "geometryType": "point"},
                    {"classTitle": "crown", "geometryType": "point"},
                ]},
            },
        )
        db.add(sample)
        db.flush()
        sample_id, token, team_id = sample.id, sample.claim_token, team.id

    rubric = client.get("/api/v1/scoring/rubric")
    assert rubric.status_code == 200, rubric.text
    assert rubric.json()["weights"] == {
        "visible_keypoint": 1, "occluded_keypoint": 3, "box": 1,
        "wrong_class": 1, "wrong_placement": 2, "sabotage": 3,
    }
    graded = act(client, csrf, f"/review-queue/{sample_id}/complete", {
        "claim_token": token, "action": "pause",
        "verdicts": [
            {"object_index": 0, "correct": True},
            {"object_index": 1, "correct": True},
            {"object_index": 2, "correct": True},
            {"object_index": 3, "correct": False, "reason": "wrong_class",
             "corrected_class": "right_eye_occ"},
            {"object_index": 4, "correct": False, "reason": "wrong_placement"},
            {"object_index": 5, "correct": False, "reason": "sabotage"},
        ],
    })
    assert graded.status_code == 200, graded.text
    with maker() as db:
        decided = db.get(ReviewSample, sample_id).decision
        assert decided["correct_keypoints"] == 2
        assert decided["correct_visible_keypoints"] == 1
        assert decided["correct_occluded_keypoints"] == 1
        assert decided["correct_boxes"] == 1
        assert decided["errors"] == {
            "wrong_class": 1, "wrong_placement": 1, "sabotage": 1,
        }
        assert decided["weighted_score"] == -1  # 1 + 3 + 1 - 1 - 2 - 3

    before = client.get("/api/v1/rankings/teams").json()["items"]
    own = next(row for row in before if row["team_id"] == team_id)
    assert own["correct_objects"] == 3
    assert own["weighted_score"] == -1
    assert own["correct_occluded_keypoints"] == 1

    changed_weights = {
        "visible_keypoint": 2, "occluded_keypoint": 5, "box": 3,
        "wrong_class": 2, "wrong_placement": 4, "sabotage": 6,
    }
    changed = act(client, csrf, "/scoring/rubric", changed_weights, method="PUT")
    assert changed.status_code == 200, changed.text
    assert changed.json()["weights"] == changed_weights
    after = client.get("/api/v1/rankings/teams").json()["items"]
    own = next(row for row in after if row["team_id"] == team_id)
    assert own["weighted_score"] == -2  # 2 + 5 + 3 - 2 - 4 - 6
    with maker() as db:
        # Already reviewed verdicts are never overwritten when the rubric changes.
        assert db.get(ReviewSample, sample_id).decision["weighted_score"] == -1

    monitor_login = client.post("/api/v1/auth/login", json={
        "email": "weights-monitor@example.org",
        "password": "StrongMonitorPassword123!",
    })
    assert monitor_login.status_code == 200, monitor_login.text
    assert client.get("/api/v1/scoring/rubric").status_code == 200
    rejected = act(client, monitor_login.json()["csrf_token"],
                   "/scoring/rubric", changed_weights, method="PUT")
    assert rejected.status_code == 403


def test_prize_ranking_uses_equal_points_and_preserves_weighted_metric(dataset_ctx):
    client, maker, csrf = dataset_ctx
    teams(client, csrf)
    with maker.begin() as db:
        organiser = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Ranking source",
            upload_kind="folder", root_path="/tmp/ranking",
            created_by=organiser, status="ready",
        )
        db.add(dataset)
        db.flush()
        plan = LabelingPlan(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            jobs_per_team=0, redundancy=1, created_by=organiser,
            queue_seed="weighted-ranking", status="running",
        )
        db.add(plan)
        db.flush()
        all_teams = list(db.scalars(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
        ).order_by(CompetitionTeam.name)))
        for index, team in enumerate(all_teams[:2]):
            person = db.scalar(select(Person).where(Person.team_id == team.id))
            asset = MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                relative_path=f"ranking/{index}.jpg", morphology="bird",
                kind="image", size_bytes=1, sha256="0" * 64,
            )
            db.add(asset)
            db.flush()
            job = LabelingJob(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                plan_id=plan.id, team_id=team.id, sequence=1,
                assignee_person_id=person.id, release_mode="shared_primary",
                media_ids=[asset.id], frame_count=1, video_count=1,
                morphology_counts={"bird": 1}, status="on_review",
                correction_round=0,
            )
            db.add(job)
            db.flush()
            # Team 0 has more correct objects (3 visible);
            # Team 1 scores more (one correct occluded + one box = 4).
            if index == 0:
                objects = [
                    {"classTitle": c, "geometryType": "point"}
                    for c in ("left_eye", "right_eye", "beak")
                ]
                kp, box = 3, 0
            else:
                objects = [
                    {"classTitle": "left_eye_occ", "geometryType": "point"},
                    {"classTitle": "bird", "geometryType": "rectangle"},
                ]
                kp, box = 1, 1
            decision = {
                "correct_keypoints": kp, "correct_boxes": box,
                "errors": {}, "missing_classes": [],
                "verdicts": [
                    {"object_index": i, "correct": True} for i in range(len(objects))
                ],
            }
            snapshot = {"sha256": "0" * 64, "source_relative_path": asset.relative_path,
                        "selection_reason": "submitted_frame",
                        "annotation": {"objects": objects}}
            decision["weighted_score"] = score_verdict(
                decision, snapshot, resolved_weights(None),
            )
            db.add(ReviewSample(
                event_id=DEFAULT_EVENT_ID, job_id=job.id, team_id=team.id,
                media_id=asset.id, correction_round=0,
                snapshot=snapshot, decision=decision, completed_at=epoch(),
            ))
        lower_count_team, higher_score_team = all_teams[0].id, all_teams[1].id

    response = client.get("/api/v1/rankings/teams")
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert rows[0]["team_id"] == lower_count_team
    assert rows[0]["estimated_points"] == 3
    weighted_leader = next(row for row in rows if row["team_id"] == higher_score_team)
    assert weighted_leader["estimated_points"] == 2
    assert weighted_leader["weighted_score"] == 4
    assert weighted_leader["correct_objects"] == 2
    assert weighted_leader["coverage_pct"] == 100
    assert weighted_leader["submission_share_pct"] == 50
    assert weighted_leader["review_share_pct"] == 50
    count_leader = next(row for row in rows if row["team_id"] == lower_count_team)
    assert count_leader["correct_objects"] == 3
    assert count_leader["weighted_score"] == 3



def test_prize_points_count_each_anatomical_item_at_most_once():
    from annotatiathon.scoring_api import frame_points

    classes = ["bird", "bird", "left_eye", "left_eye_occ", "right_eye", "beak", "crown"]
    snapshot = {"annotation": {"objects": [{"classTitle": title} for title in classes]}}
    verdicts = [{"object_index": index, "correct": True} for index in range(len(classes))]
    assert frame_points({"verdicts": verdicts}, snapshot) == 5
    verdicts[-1]["correct"] = False
    assert frame_points({"verdicts": verdicts}, snapshot) == 4
    assert frame_points({"verdicts": []}, snapshot) == 0
