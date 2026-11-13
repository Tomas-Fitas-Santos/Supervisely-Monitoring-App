"""ZIP video plan -> mounted images, without real accounts or remote writes."""
import io
from types import SimpleNamespace

import pytest
import test_native_dispatch as native
from PIL import Image
from sqlalchemy import func, select
from test_import_job_review_lifecycle import import_source
from test_import_job_review_lifecycle import workflow as lifecycle_workflow

from annotatiathon import models as m
from annotatiathon.balanced_queue import process_preparation_once
from annotatiathon.frame_submission import capture_frame_submission
from annotatiathon.mounted_datasets import process_mounted_index_once
from annotatiathon.native_worker import (
    process_correction_once,
    process_mirror_once,
    process_once,
    queue_automatic_incomplete_return,
)
from annotatiathon.plan_sequence import activate_waiting_plans
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    yield from lifecycle_workflow.__wrapped__(monkeypatch, tmp_path)


def setup_sequence(workflow, tmp_path, mode="shared_30_v1", approve_video=True):
    http, maker, csrf, _, sdk = workflow
    videos, _ = import_source(http, maker, csrf, tmp_path, "zip", sdk)
    if mode == "balanced_30_v1":
        for folder in ("SpeciesA", "SpeciesB"):
            assert http.put(f"/api/v1/coverage/datasets/{videos}/classification",
                json={"source_folder": folder, "ecomorphology": "General perching birds"},
                headers={"X-CSRF-Token": csrf}).status_code == 200
        assert native.call(http, csrf, f"/datasets/{videos}/balanced-preparation").status_code == 202
        with maker() as db:
            assert process_preparation_once(db)["status"] == "done"
    response = native.call(http, csrf, "/labeling/plans", {"dataset_id": videos, "mode": mode})
    assert response.status_code == 201, response.text
    parent = response.json()["plan_id"]
    if approve_video:
        assert native.call(http, csrf, f"/labeling/plans/{parent}/approve").status_code == 200
    raw = io.BytesIO()
    Image.new("RGB", (100, 100), "green").save(raw, format="JPEG")
    folder = tmp_path / "sources" / "stills" / "Unknown category"
    folder.mkdir(parents=True)
    for i in range(33):
        (folder / f"photo_{i:03d}.jpg").write_bytes(raw.getvalue())
    response = native.call(http, csrf, "/datasets/mounted", {
        "folder": "stills", "name": "Still images", "extraction_policy": "all_frames"})
    assert response.status_code == 202, response.text
    images = response.json()["id"]
    with maker() as db:
        assert process_mounted_index_once(db)["status"] == "done"
        for _ in range(2):
            assert process_mirror_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert db.get(m.MediaDataset, images).image_count == 33
    response = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": images, "mode": "images_30_v1", "after_plan_id": parent})
    assert response.status_code == 201, response.text
    child = response.json()["plan_id"]
    assert response.json()["primary_blocks"] == 2
    assert native.call(http, csrf, f"/labeling/plans/{child}/approve").status_code == 200
    for _ in range(2):
        armed = native.call(http, csrf, "/native/launch", {"plan_id": child})
        assert armed.status_code == 200, armed.text
        assert armed.json()["status"] == "waiting"
    with maker.begin() as db:
        person = db.scalar(select(m.Person).order_by(m.Person.id))
        person.check_in_at = 1
        person_id, team_id = person.id, person.team_id
    return parent, child, videos, images, person_id, team_id, raw.getvalue()


@pytest.mark.parametrize("mode", ["shared_30_v1", "balanced_30_v1"])
def test_zip_videos_then_mounted_images_after_validated_submissions(workflow, tmp_path, mode, monkeypatch):
    http, maker, csrf, _, sdk = workflow
    parent, child, _videos, images, person, team, raw = setup_sequence(workflow, tmp_path, mode)
    uploaded = len(sdk.upload_history)
    with maker() as db:
        activate_waiting_plans(db)
        assert db.get(m.LabelingPlan, child).status == "waiting"
        assert claim_primary_block(db, child, team, person) is None
    # Arming does not launch, replace or cancel the existing video plan.
    with maker() as db:
        assert db.get(m.LabelingPlan, parent).status == "approved"
    assert native.call(http, csrf, "/native/launch", {"plan_id": parent}).status_code == 200
    for expected_size in (30, 30, 5):
        with maker() as db:
            job = claim_primary_block(db, parent, team, person)
            assert job is not None and job.frame_count == expected_size
            db.commit()
            assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
            # A remote Submit flag alone must not unlock the image plan.
            job.status = "on_review"
            job.submitted_at = 1
            db.commit()
            activate_waiting_plans(db)
            assert db.get(m.LabelingPlan, child).status == "waiting"
            result = capture_frame_submission(db, sdk, job)
            assert result["pending"] == []
            db.commit()
    # All source IDs validated, no monitor review completed yet.
    with maker() as db:
        assert db.scalar(select(func.count()).select_from(m.ReviewSample).where(
            m.ReviewSample.completed_at.is_not(None))) == 0
        # An unavailable image mirror prevents activation even after video completion.
        ds = db.get(m.MediaDataset, images)
        ds.mirrored_revision = 0
        db.commit()
        activate_waiting_plans(db)
        assert db.get(m.LabelingPlan, child).status == "waiting"
        ds.mirrored_revision = ds.inventory_revision
        db.commit()
        activate_waiting_plans(db)
        activate_waiting_plans(db)
        assert db.get(m.LabelingPlan, child).status == "running"
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.action == "labeling.plan.sequence.start")) == 1
        job = claim_primary_block(db, child, team, person)
        assert job.frame_count == 30 and job.video_count == 0
        image_job_id = job.id
        db.commit()
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert len(sdk.upload_history) == uploaded
        full = sdk.labeling_job.get_annotations

        def partial(job_id, **kwargs):
            annotations = full(job_id, **kwargs)
            value = annotations[-1].to_json()
            value["objects"] = value["objects"][:1]
            annotations[-1] = SimpleNamespace(to_json=lambda: value)
            return annotations

        sdk.labeling_job.get_annotations = partial
        result = capture_frame_submission(db, sdk, job)
        assert len(result["pending"]) == 1
        db.commit()
        assert claim_primary_block(db, child, team, person) is None
        queue_automatic_incomplete_return(db, job, 29, 30, pending_media_ids=result["pending"])
        assert process_correction_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        sdk.labeling_job.get_annotations = full
        assert capture_frame_submission(db, sdk, job)["pending"] == []
        db.commit()
        tail = claim_primary_block(db, child, team, person)
        assert tail.frame_count == 3 and tail.video_count == 0
        assert set(tail.media_ids).isdisjoint(job.media_ids)
        db.commit()
        assert db.get(m.LabelingPlan, parent).status == "running"
    # Mounted images use the same authorised review preview and completion pipeline.
    with maker() as db:
        sample = db.scalar(select(m.ReviewSample).where(m.ReviewSample.job_id == image_job_id))
        sample_id, media_id = sample.id, sample.media_id
        pivot = sample.random_key
    preview = http.get(f"/api/v1/review/datasets/{images}/assets/{media_id}/image")
    assert preview.status_code == 200 and preview.content == raw
    with maker() as db:
        assert db.get(m.ReviewSample, sample_id).validated_complete
    monkeypatch.setattr("annotatiathon.review_queue_api.identifier", lambda: pivot)
    claimed = native.call(http, csrf, "/review-queue/claim-next")
    assert claimed.status_code == 200, claimed.text
    review = claimed.json()["review"]
    assert review["source_media_id"] == media_id
    verdict = {"claim_token": review["claim_token"], "verdicts": [
        {"object_index": i, "correct": True} for i in range(len(review["annotation"]["objects"]))]}
    completed = native.call(http, csrf, f"/review-queue/{review['review_id']}/complete", verdict)
    assert completed.status_code == 200, completed.text
    ranking = next(r for r in http.get("/api/v1/rankings/teams").json()["items"] if r["team_id"] == team)
    assert (ranking["reviewed_frames"], ranking["correct_boxes"], ranking["correct_keypoints"]) == (1, 1, 4)
    # Inventory is frozen while either automatic plan can assign jobs.
    assert native.call(http, csrf, f"/datasets/{images}/mounted/sync").status_code == 409


def test_sequence_guards_and_cancel_only_unstarted_image_plan(workflow, tmp_path):
    http, maker, csrf, _, _sdk = workflow
    parent, child, _videos, images, person, team, _ = setup_sequence(workflow, tmp_path)
    assert native.call(http, csrf, f"/native/plans/{child}/resume").status_code == 409
    duplicate = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": images, "mode": "images_30_v1", "after_plan_id": parent})
    assert duplicate.status_code == 409
    assert native.call(http, csrf, f"/labeling/plans/{child}/cancel").status_code == 200
    with maker() as db:
        activate_waiting_plans(db)
        assert db.get(m.LabelingPlan, parent).status == "approved"
        assert db.get(m.LabelingPlan, child).status == "cancelled"
        assert claim_primary_block(db, child, team, person) is None
        assert db.scalar(select(func.count()).select_from(m.LabelingJob).where(
            m.LabelingJob.plan_id == child)) == 0
    wrong_mode = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": images, "mode": "shared_30_v1", "after_plan_id": parent})
    assert wrong_mode.status_code == 422


def test_image_plan_can_wait_for_existing_video_draft(workflow, tmp_path):
    http, maker, csrf, _, _sdk = workflow
    parent, child, videos, _images, _person, _team, _raw = setup_sequence(
        workflow, tmp_path, approve_video=False)
    with maker() as db:
        assert db.get(m.LabelingPlan, parent).status == "planned"
        before = list(db.scalars(select(m.WorkBlock.id).where(m.WorkBlock.plan_id == parent)))
        activate_waiting_plans(db)
        assert db.get(m.LabelingPlan, child).status == "waiting"
    assert native.call(http, csrf, f"/datasets/{videos}/append-batches", {
        "upload_kind": "zip"}).status_code == 409
    approved = native.call(http, csrf, f"/labeling/plans/{parent}/approve")
    assert approved.status_code == 200, approved.text
    with maker() as db:
        assert list(db.scalars(select(m.WorkBlock.id).where(m.WorkBlock.plan_id == parent))) == before
        assert db.get(m.LabelingPlan, parent).after_plan_id is None
