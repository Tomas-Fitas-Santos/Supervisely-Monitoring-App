"""NABirds-style nested photos: correct metadata and chain after running video work.

All media, databases and Supervisely calls are isolated test fixtures.
"""
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select
import test_native_dispatch as native
from test_import_job_review_lifecycle import import_source, workflow as lifecycle_workflow

from annotatiathon import models as m
from annotatiathon.dataset_indexing import process_index_once
from annotatiathon.frame_submission import capture_frame_submission
from annotatiathon.native_media_pool import mirror_status
from annotatiathon.native_worker import process_mirror_once, process_once
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    yield from lifecycle_workflow.__wrapped__(monkeypatch, tmp_path)


def import_nested_photos(workflow):
    http, maker, csrf, _, sdk = workflow
    raw = io.BytesIO()
    Image.new("RGB", (100, 100), "green").save(raw, format="JPEG")
    response = native.call(http, csrf, "/datasets/uploads", {
        "name": "NABirds", "upload_kind": "folder", "extraction_policy": "all_frames"})
    assert response.status_code == 201
    dataset = response.json()["id"]
    for number in range(33):
        response = http.put(f"/api/v1/datasets/uploads/{dataset}/chunk", content=raw.getvalue(),
            params={"relative_path": f"birds/NABirds/0297/ab{number:030x}.jpg",
                    "offset": 0, "total": len(raw.getvalue())},
            headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200, response.text
    assert native.call(http, csrf, f"/datasets/{dataset}/finalize?background=true").status_code == 202
    with maker() as db:
        assert process_index_once(db)["status"] == "done"
        assert process_mirror_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        ds = db.get(m.MediaDataset, dataset)
        assert (ds.image_count, ds.frame_count, ds.video_count) == (0, 33, 1)
        assert mirror_status(db, ds)["ready_for_automatic_jobs"]
    return dataset


def inventory(db, dataset):
    return {a.id: (a.relative_path, a.sha256, a.size_bytes, a.native_image_id,
                   a.native_dataset_id) for a in db.scalars(select(m.MediaAsset).where(
                       m.MediaAsset.dataset_id == dataset))}


def test_nested_photos_correct_and_arm_after_running_video_plan(workflow, tmp_path):
    http, maker, csrf, _, sdk = workflow
    videos, _ = import_source(http, maker, csrf, tmp_path, "zip", sdk)
    with maker() as db:
        assert process_mirror_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
    parent = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": videos, "mode": "shared_30_v1"}).json()["plan_id"]
    assert native.call(http, csrf, f"/labeling/plans/{parent}/approve").status_code == 200
    assert native.call(http, csrf, "/native/launch", {"plan_id": parent}).status_code == 200
    photos = import_nested_photos(workflow)
    uploaded = len(sdk.upload_history)
    with maker() as db:
        before = inventory(db, photos)
        bindings = list(db.execute(select(m.NativeSourceMedia.media_id, m.NativeSourceMedia.native_image_id)
                                  .where(m.NativeSourceMedia.dataset_id == photos)))
        parent_blocks = list(db.scalars(select(m.WorkBlock.id).where(m.WorkBlock.plan_id == parent)))
        revision = db.get(m.MediaDataset, photos).inventory_revision
    payload = {"dataset_id": photos, "mode": "images_30_v1", "after_plan_id": parent}
    assert native.call(http, csrf, "/labeling/plans", payload).status_code == 422
    response = native.call(http, csrf, "/labeling/plans", {**payload, "confirm_standalone_images": True})
    assert response.status_code == 201, response.text
    child = response.json()["plan_id"]
    assert response.json()["primary_blocks"] == 2
    with maker() as db:
        ds = db.get(m.MediaDataset, photos)
        assert (ds.image_count, ds.frame_count, ds.video_count) == (33, 0, 0)
        assert ds.inventory_revision == ds.mirrored_revision == revision + 1
        assert mirror_status(db, ds)["ready_for_automatic_jobs"]
        assert inventory(db, photos) == before
        assert list(db.execute(select(m.NativeSourceMedia.media_id, m.NativeSourceMedia.native_image_id)
                               .where(m.NativeSourceMedia.dataset_id == photos))) == bindings
        assert all((Path(ds.root_path) / "media" / entry[0]).is_file() for entry in before.values())
        assets = list(db.scalars(select(m.MediaAsset).where(m.MediaAsset.dataset_id == photos)))
        assert all(a.kind == "image" and a.video_id is None and a.video_group is None
                   and a.frame_index is None for a in assets)
        assert db.scalar(select(func.count()).select_from(m.MediaVideo).where(m.MediaVideo.dataset_id == photos)) == 0
        blocks = list(db.scalars(select(m.WorkBlock).where(m.WorkBlock.plan_id == child).order_by(m.WorkBlock.ordinal)))
        assert [b.frame_count for b in blocks] == [30, 3]
        assert all(b.video_id is None for b in blocks)
        assert {mid for b in blocks for mid in b.media_ids} == set(before)
        assert db.get(m.LabelingPlan, parent).status == "running"
        assert list(db.scalars(select(m.WorkBlock.id).where(m.WorkBlock.plan_id == parent))) == parent_blocks
    assert native.call(http, csrf, f"/labeling/plans/{child}/approve").status_code == 200
    armed = native.call(http, csrf, "/native/launch", {"plan_id": child})
    assert armed.status_code == 200, armed.text
    assert armed.json()["status"] == "waiting"
    assert len(sdk.upload_history) == uploaded
    assert http.get(f"/api/v1/datasets/{photos}/videos").json()["items"] == []
    # The corrected assets must also pass the worker's image-identity checks
    # and reuse their mirrored IDs when the running video queue hands off.
    with maker.begin() as db:
        person = db.scalar(select(m.Person).order_by(m.Person.id))
        person.check_in_at = 1
        person_id, team_id = person.id, person.team_id
    for expected in (30, 30, 5):
        with maker() as db:
            job = claim_primary_block(db, parent, team_id, person_id)
            assert job.frame_count == expected and job.dataset_id == videos
            db.commit()
            assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
            assert capture_frame_submission(db, sdk, job)["pending"] == []
            db.commit()
    with maker() as db:
        job = claim_primary_block(db, parent, team_id, person_id)
        assert job.dataset_id == photos and job.plan_id == child
        assert job.frame_count == 30 and job.video_count == 0
        db.commit()
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert capture_frame_submission(db, sdk, job)["pending"] == []
        db.commit()
        assert db.get(m.LabelingPlan, parent).status == "running"
        assert len(sdk.upload_history) == uploaded


@pytest.mark.parametrize("blocker", ["manifest", "original_video", "existing_plan", "append", "mirror", "mode"])
def test_correction_refuses_unsafe_or_unsupported_sources(workflow, blocker):
    http, maker, csrf, _, _sdk = workflow
    photos = import_nested_photos(workflow)
    with maker.begin() as db:
        ds = db.get(m.MediaDataset, photos)
        video = db.scalar(select(m.MediaVideo).where(m.MediaVideo.dataset_id == photos))
        if blocker == "manifest":
            video.extraction_policy = "supplied_manifest"
        elif blocker == "original_video":
            video.source_sha256 = "a" * 64
        elif blocker == "existing_plan":
            db.add(m.LabelingPlan(event_id=ds.event_id, dataset_id=photos, jobs_per_team=0, redundancy=1,
                                 created_by=ds.created_by, status="cancelled"))
        elif blocker == "append":
            db.add(m.DatasetAppendBatch(event_id=ds.event_id, dataset_id=photos,
                                       upload_kind="folder", revision_at_start=ds.inventory_revision,
                                       created_by=ds.created_by))
        elif blocker == "mirror":
            ds.mirrored_revision = 0
        before = inventory(db, photos)
    response = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": photos, "mode": "shared_30_v1" if blocker == "mode" else "images_30_v1",
        "confirm_standalone_images": True})
    assert response.status_code in {409, 422}, response.text
    with maker() as db:
        ds = db.get(m.MediaDataset, photos)
        assert (ds.image_count, ds.frame_count, ds.video_count) == (0, 33, 1)
        assert inventory(db, photos) == before
        assert db.scalar(select(func.count()).select_from(m.MediaAsset).where(
            m.MediaAsset.dataset_id == photos, m.MediaAsset.kind == "video_frame")) == 33
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.target_id == photos, m.AuditEvent.action == "dataset.reclassify_standalone_images")) == 0


def test_plan_failure_rolls_back_photo_correction(workflow, monkeypatch):
    http, maker, csrf, _, _sdk = workflow
    photos = import_nested_photos(workflow)
    def fail_block(**kwargs):
        raise RuntimeError("simulated plan failure")
    monkeypatch.setattr("annotatiathon.labeling_api.WorkBlock", fail_block)
    with pytest.raises(RuntimeError, match="simulated plan failure"):
        native.call(http, csrf, "/labeling/plans", {
            "dataset_id": photos, "mode": "images_30_v1", "confirm_standalone_images": True})
    with maker() as db:
        ds = db.get(m.MediaDataset, photos)
        assert (ds.image_count, ds.frame_count, ds.video_count) == (0, 33, 1)
        assert db.scalar(select(func.count()).select_from(m.MediaVideo).where(m.MediaVideo.dataset_id == photos)) == 1
        assert db.scalar(select(func.count()).select_from(m.MediaAsset).where(
            m.MediaAsset.dataset_id == photos, m.MediaAsset.video_id.is_not(None))) == 33
        assert db.scalar(select(func.count()).select_from(m.LabelingPlan).where(m.LabelingPlan.dataset_id == photos)) == 0
