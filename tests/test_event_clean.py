"""Dataset-preserving reset uses disposable state and a fake remote SDK only."""
import hashlib
import json
from pathlib import Path

import pytest
import test_native_dispatch as native
from sqlalchemy import select, text
from test_import_job_review_lifecycle import workflow as lifecycle_workflow
from test_sequential_image_plans import setup_sequence

from annotatiathon import models as m
from annotatiathon.frame_submission import capture_frame_submission
from annotatiathon.full_reset_api import DATASET_TABLES
from annotatiathon.native_media_pool import mirror_status
from annotatiathon.native_names import owned_project_event_id
from annotatiathon.native_worker import process_event_reset, process_once
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    yield from lifecycle_workflow.__wrapped__(monkeypatch, tmp_path)


def support_clear(sdk):
    sdk.annotations = {}
    sdk.annotation_batches = []
    owned = {p.id for p in sdk.projects if owned_project_event_id(p.name) == m.DEFAULT_EVENT_ID}
    for images in sdk.images.values():
        for image in images:
            image.width = image.height = 100
            sdk.annotations[image.id] = {"objects": [{"test": True}], "tags": [{"test": True}]}

    def upload(ids, annotations):
        assert not [job for job in sdk.jobs if job.project_id in owned]
        sdk.annotation_batches.append(list(ids))
        for image_id, annotation in zip(ids, annotations):
            sdk.annotations[image_id] = annotation
    sdk.annotation.upload_jsons = upload


def snapshot(db):
    state = {}
    for name in sorted(DATASET_TABLES):
        table = m.Base.metadata.tables[name]
        state[name] = sorted([dict(row) for row in db.execute(select(table)).mappings()],
                             key=lambda row: json.dumps(row, sort_keys=True))
    for model in (m.Person, m.CompetitionTeam):
        table = model.__table__
        state[table.name] = [{k: v for k, v in dict(row).items()
                              if k not in {"check_in_at", "table_number", "updated_at"}}
                             for row in db.execute(select(table).order_by(table.c.id)).mappings()]
    return state


def request_clean(http, csrf):
    preview = http.get("/api/v1/settings/reset/preview?preserve_datasets=true").json()
    assert preview["preserve_datasets"] and preview["confirmation_phrase"].startswith("CLEAN ")
    result = native.call(http, csrf, "/settings/reset/request", {
        "confirmation": preview["confirmation_phrase"], "preserve_datasets": True})
    assert result.status_code == 202, result.text


@pytest.mark.parametrize("mode", ["shared_30_v1", "balanced_30_v1"])
def test_clean_keeps_zip_and_mounted_data_and_can_recreate_sequence(workflow, tmp_path, mode):
    http, maker, csrf, _, sdk = workflow
    parent, child, videos, images, person, team, _ = setup_sequence(workflow, tmp_path, mode)
    # Exercise actual SQL FK ordering, including linked parent/child plans.
    with maker() as db:
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()
    assert native.call(http, csrf, "/native/launch", {"plan_id": parent}).status_code == 200
    with maker() as db:
        job = claim_primary_block(db, parent, team, person)
        db.commit()
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert capture_frame_submission(db, sdk, job)["pending"] == []
        db.commit()
        sample = db.scalar(select(m.ReviewSample).where(m.ReviewSample.job_id == job.id))
        sample.completed_at = 1
        sample.decision = {"correct_boxes": 1, "correct_keypoints": 4, "missing_classes": [], "errors": {}}
        job = claim_primary_block(db, parent, team, person)
        db.commit()
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        db.get(m.Person, person).table_number = 3
        db.get(m.CompetitionTeam, team).table_number = 3
        db.commit()
        before = snapshot(db)
    support_clear(sdk)
    source_files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tmp_path.rglob("*.jpg")}
    uploads = len(sdk.upload_history)
    assert any(row["reviewed_frames"] > 0 for row in http.get("/api/v1/rankings/teams").json()["items"])
    category = native.call(http, csrf, "/tickets/categories", {"slug": "pizza", "name": "Pizza"})
    assert category.status_code == 201, category.text
    with maker.begin() as db:
        staff = db.scalar(select(m.StaffAccount.id))
        distribution = m.TicketDistribution(event_id=m.DEFAULT_EVENT_ID,
            category_id=category.json()["id"], operation="allocate", quantity=2,
            recipient_ids=[person], scope="selected", status="confirmed", requested_by=staff)
        db.add(distribution)
        db.flush()
        db.add(m.TicketLedgerEntry(event_id=m.DEFAULT_EVENT_ID,
            category_id=category.json()["id"], person_id=person, distribution_id=distribution.id,
            kind="allocated", quantity=2, staff_id=staff))
        activity = m.Activity(event_id=m.DEFAULT_EVENT_ID, name="Test prize", created_by=staff)
        db.add(activity)
        db.flush()
        db.add(m.ActivityResult(event_id=m.DEFAULT_EVENT_ID, activity_id=activity.id,
            subject_type="team", subject_id=team, rank=1, score_points=10, recorded_by=staff))
    request_clean(http, csrf)
    assert native.call(http, csrf, "/native/launch", {"plan_id": parent}).status_code == 409
    with maker() as db:
        result = process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))
        assert result["status"] == "done", db.scalar(select(m.NativeOperation.error_code).where(
            m.NativeOperation.kind == "reset_event"))
        assert snapshot(db) == before
        for model in (m.LabelingPlan, m.LabelingJob, m.WorkBlock, m.WorkPacket, m.ReviewSample,
                      m.TicketLedgerEntry, m.TicketDistribution, m.Activity, m.ActivityResult):
            assert not list(db.scalars(select(model)))
        assert db.get(m.Person, person).check_in_at is None
        assert db.get(m.Person, person).table_number is None
        assert db.get(m.CompetitionTeam, team).table_number is None
        for dsid in (videos, images):
            assert mirror_status(db, db.get(m.MediaDataset, dsid))["ready_for_automatic_jobs"]
    assert not sdk.jobs and not sdk.removed_projects and not sdk.removed_datasets
    assert sdk.annotations and all(not a["objects"] and not a["tags"] for a in sdk.annotations.values())
    assert {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in source_files} == source_files
    assert http.get("/api/v1/attendance/summary").json()["present"] == 0
    assert all(row["reviewed_frames"] == 0 for row in http.get("/api/v1/rankings/teams").json()["items"])
    # Reuse the exact datasets and mirror IDs to create and publish a fresh plan.
    first = native.call(http, csrf, "/labeling/plans", {"dataset_id": videos, "mode": mode})
    assert first.status_code == 201, first.text
    new_parent = first.json()["plan_id"]
    assert new_parent != parent
    assert native.call(http, csrf, f"/labeling/plans/{new_parent}/approve").status_code == 200
    second = native.call(http, csrf, "/labeling/plans", {
        "dataset_id": images, "mode": "images_30_v1", "after_plan_id": new_parent})
    assert second.status_code == 201, second.text
    assert second.json()["plan_id"] != child
    assert native.call(http, csrf, "/native/launch", {"plan_id": new_parent}).status_code == 200
    with maker() as db:
        assert claim_primary_block(db, new_parent, team, person) is None
        db.get(m.Person, person).check_in_at = 1
        db.commit()
        assert claim_primary_block(db, new_parent, team, person) is not None
        db.commit()
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
    assert len(sdk.upload_history) == uploads


@pytest.fixture
def configured(monkeypatch, tmp_path):
    yield from native.configured.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("failure", ["remove_job", "clear_annotations", "worker_crash"])
def test_clean_retries_without_losing_dataset_or_repeating_completed_batches(configured, failure):
    http, maker, csrf, plan, sdk = configured
    native.launch(http, csrf, plan)
    with maker() as db:
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        before = snapshot(db)
    support_clear(sdk)
    request_clean(http, csrf)
    original = sdk.labeling_job.remove if failure == "remove_job" else sdk.annotation.upload_jsons

    def interrupted(*args):
        original(*args)
        raise ConnectionError("Lost response after remote write")
    if failure == "remove_job":
        sdk.labeling_job.remove = interrupted
    elif failure == "clear_annotations":
        sdk.annotation.upload_jsons = interrupted
    with maker() as db:
        if failure == "worker_crash":
            operation = db.scalar(select(m.NativeOperation).where(m.NativeOperation.kind == "reset_event"))
            operation.status = "running"
            db.commit()
        else:
            assert process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "attention"
            assert snapshot(db) == before
            assert db.get(m.Event, m.DEFAULT_EVENT_ID).status == "resetting"
            assert list(db.scalars(select(m.LabelingJob)))
    sdk.labeling_job.remove = sdk.jobs_remove
    if failure == "clear_annotations":
        sdk.annotation.upload_jsons = original
    if failure != "worker_crash":
        assert native.call(http, csrf, "/settings/reset/retry").status_code == 202
    with maker() as db:
        assert process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        assert snapshot(db) == before
        assert process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1)) is None
    assert not sdk.removed_projects and not sdk.removed_datasets


def test_clean_refuses_wrong_phrase_unfinished_import_or_changed_remote_owner(configured):
    http, maker, csrf, plan, sdk = configured
    wrong = native.call(http, csrf, "/settings/reset/request", {
        "confirmation": "RESET Official event", "preserve_datasets": True})
    assert wrong.status_code == 422
    with maker.begin() as db:
        db.get(m.MediaDataset, "dataset01").status = "uploading"
    blocked = native.call(http, csrf, "/settings/reset/request", {
        "confirmation": "CLEAN Official event", "preserve_datasets": True})
    assert blocked.status_code == 409
    with maker.begin() as db:
        db.get(m.MediaDataset, "dataset01").status = "ready"
    native.launch(http, csrf, plan)
    with maker() as db:
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
    request_clean(http, csrf)
    sdk.projects[0].name = "Unrelated research"
    with maker() as db:
        assert process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "attention"
        assert list(db.scalars(select(m.LabelingJob)))
        assert db.get(m.MediaDataset, "dataset01") is not None
    assert not sdk.removed_jobs and not sdk.removed_datasets and not sdk.removed_projects


def test_clean_resumes_annotations_after_committed_batch(configured):
    http, maker, csrf, _, sdk = configured
    native_ds = sdk.datasets[0].id
    with maker.begin() as db:
        for index in range(410):
            asset = m.MediaAsset(event_id=m.DEFAULT_EVENT_ID, dataset_id="dataset01",
                relative_path=f"images/unknown/{index}.jpg", morphology="unknown", kind="image",
                size_bytes=1, sha256="0"*64, frame_width=100, frame_height=100)
            db.add(asset)
            db.flush()
            image_id = sdk.new_id()
            sdk.images[native_ds].append(native.Obj(id=image_id, name=f"{asset.id}.jpg"))
            db.add(m.NativeSourceMedia(event_id=m.DEFAULT_EVENT_ID, dataset_id="dataset01",
                media_id=asset.id, native_dataset_id=native_ds, native_image_id=image_id))
        db.get(m.MediaDataset, "dataset01").image_count = 413
    support_clear(sdk)
    upload = sdk.annotation.upload_jsons

    def fail_second_batch(ids, annotations):
        upload(ids, annotations)
        if len(sdk.annotation_batches) == 2:
            raise ConnectionError("Remote write succeeded but its acknowledgement was lost")
    sdk.annotation.upload_jsons = fail_second_batch
    request_clean(http, csrf)
    with maker() as db:
        assert process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "attention"
        operation = db.scalar(select(m.NativeOperation).where(m.NativeOperation.kind == "reset_event"))
        assert operation.payload["cleared_annotations"] == 200
        # Simulate a crash after a retry was claimed; the recovery worker can
        # reclaim it only after obtaining the exclusive cross-process barrier.
        operation.status = "running"
        db.commit()
    sdk.annotation.upload_jsons = upload
    with maker() as db:
        result = process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))
        assert result["status"] == "done" and result["cleared_annotations"] == 413
        assert db.get(m.MediaDataset, "dataset01").image_count == 413
    assert [len(batch) for batch in sdk.annotation_batches] == [200, 200, 200, 13]
    assert sdk.annotation_batches[1] == sdk.annotation_batches[2]
    assert set(sdk.annotation_batches[0]).isdisjoint(sdk.annotation_batches[2])


def test_clean_removes_correction_history_but_keeps_other_event_and_remote_work(configured):
    http, maker, csrf, plan, sdk = configured
    native.launch(http, csrf, plan)
    with maker() as db:
        assert process_once(db, sdk, db.get(m.SuperviselyConfig, 1))["status"] == "done"
        binding = db.scalar(select(m.NativeJobBinding))
        old = sdk.labeling_job.get_info_by_id(binding.native_job_id)
        new = sdk.labeling_job.restart(old.id, [old.assigned_to_id], "Correction", True, False)[0]
        db.add(m.NativeJobRevision(event_id=m.DEFAULT_EVENT_ID, job_id=binding.job_id,
            person_id=binding.person_id, native_job_id=old.id, correction_round=0))
        binding.native_job_id = new["id"]
        db.add(m.Event(id="unrelated-event", name="Other event"))
        db.flush()
        db.add(m.Person(event_id="unrelated-event", name="Other person", email="other@example.test",
                        kind="participant", check_in_at=1, table_number=5))
        db.commit()
        before = snapshot(db)
    support_clear(sdk)
    other = sdk.project.create(2001, "Other research", change_name_if_conflict=False)
    other_ds = sdk.dataset.create(other.id, "Unrelated source", change_name_if_conflict=False)
    other_job = native.Obj(id=sdk.new_id(), project_id=other.id, dataset_id=other_ds.id)
    sdk.jobs.append(other_job)
    request_clean(http, csrf)
    with maker() as db:
        result = process_event_reset(db, sdk, db.get(m.SuperviselyConfig, 1))
        assert result["status"] == "done" and result["removed_native_jobs"] == 3
        assert snapshot(db) == before
        outsider = db.scalar(select(m.Person).where(m.Person.event_id == "unrelated-event"))
        assert outsider.check_in_at == 1 and outsider.table_number == 5
    assert sdk.jobs == [other_job]
    assert not sdk.removed_projects and not sdk.removed_datasets
