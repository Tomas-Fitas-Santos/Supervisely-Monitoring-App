"""Import-to-review contracts using isolated media, SQL, and the existing SDK test double."""
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_native_dispatch as native
from PIL import Image
from sqlalchemy import select

from annotatiathon.balanced_queue import process_preparation_once
from annotatiathon.dataset_indexing import process_index_once
from annotatiathon.models import (
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeMediaInstance,
    NativeOperation,
    NativeSourceMedia,
    Person,
    ReviewSample,
    SuperviselyConfig,
    WorkPacket,
)
from annotatiathon.mounted_datasets import process_mounted_index_once
from annotatiathon.mounted_sync import process_sync_once
from annotatiathon.native_worker import (
    process_correction_once,
    process_mirror_once,
    process_once,
    sync_all,
)
from annotatiathon.validation_tasks import process_validation_once
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def workflow(monkeypatch, tmp_path):
    for context in native.configured.__wrapped__(monkeypatch, tmp_path):
        with context[1].begin() as db:
            db.get(LabelingPlan, context[3]).status = "completed"
        sources = tmp_path / "sources"
        sources.mkdir()
        settings = SimpleNamespace(local_data_dir=str(tmp_path),
            nightjar_dataset_source_dir=str(sources), max_dataset_assets=250_000,
            max_dataset_bytes=140_000_000_000)
        monkeypatch.setattr("annotatiathon.datasets_api.get_settings", lambda settings=settings: settings)
        monkeypatch.setattr("annotatiathon.mounted_datasets.get_settings", lambda settings=settings: settings)
        yield context


def import_source(http, maker, csrf, tmp_path, source, sdk):
    encoded = io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(encoded, format="JPEG")
    content = encoded.getvalue()
    # The first automatic job spans two source videos; the final job is a tail.
    files = {f"{species}/clip/frame_{number:06d}.jpg": content
        for species, count in (("SpeciesA", 16), ("SpeciesB", 49))
        for number in range(1, count + 1)}
    if source in {"mounted", "mounted_sync"}:
        for relative, raw in files.items():
            if source == "mounted_sync" and relative.startswith("SpeciesB/"):
                continue
            path = tmp_path / "sources" / "birds" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        created = native.call(http, csrf, "/datasets/mounted", {
            "folder": "birds", "name": "Imported birds", "extraction_policy": "all_frames"})
        assert created.status_code == 202, created.text
        dsid = created.json()["id"]
    else:
        created = native.call(http, csrf, "/datasets/uploads", {
            "name": "Imported birds", "upload_kind": source, "extraction_policy": "all_frames"})
        assert created.status_code == 201, created.text
        dsid = created.json()["id"]
        if source == "zip":
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
                for relative, raw in files.items():
                    zipped.writestr("birds/" + relative, raw)
            uploads = {"source.zip": archive.getvalue()}
        else:
            uploads = {"birds/" + relative: raw for relative, raw in files.items()}
        for relative, raw in uploads.items():
            uploaded = http.put(f"/api/v1/datasets/uploads/{dsid}/chunk", content=raw,
                params={"relative_path": relative, "offset": 0, "total": len(raw)},
                headers={"X-CSRF-Token": csrf})
            assert uploaded.status_code == 200, uploaded.text
        assert native.call(http, csrf, f"/datasets/{dsid}/finalize?background=true").status_code == 202
    assert http.get("/api/v1/labeling/manual/options", params={"dataset_id": dsid}).status_code == 409
    assert native.call(http, csrf, "/labeling/plans", {
        "dataset_id": dsid, "mode": "shared_30_v1"}).status_code == 409
    with maker() as db:
        result = process_mounted_index_once(db) if source in {"mounted", "mounted_sync"} else process_index_once(db)
        assert result["status"] == "done", result
    if source == "mounted_sync":
        with maker() as db:
            assert process_mirror_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
            original_bindings = {row.media_id: row.native_image_id for row in db.scalars(
                select(NativeSourceMedia).where(NativeSourceMedia.dataset_id == dsid))}
            assert len(original_bindings) == 16
        for relative, raw in files.items():
            if relative.startswith("SpeciesB/"):
                path = tmp_path / "sources" / "birds" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        assert native.call(http, csrf, f"/datasets/{dsid}/mounted/sync").status_code == 202
        with maker() as db:
            assert process_sync_once(db)["status"] == "done"
            assert {row.media_id: row.native_image_id for row in db.scalars(
                select(NativeSourceMedia).where(NativeSourceMedia.dataset_id == dsid))} == original_bindings
    with maker() as db:
        ds = db.get(MediaDataset, dsid)
        assert (ds.frame_count, ds.video_count) == (65, 2)
        assets = list(db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dsid)))
        assert len(assets) == 65
        assert all(asset.sha256 == hashlib.sha256(content).hexdigest() for asset in assets)
        assert all((asset.frame_width, asset.frame_height) == (100, 100) for asset in assets)
    return dsid, content


@pytest.mark.parametrize("source", ["folder", "zip", "mounted", "mounted_sync"])
@pytest.mark.parametrize("distribution", ["manual", "shared_30_v1", "balanced_30_v1"])
def test_import_dispatch_partial_validation_correction_and_review(
    workflow, tmp_path, source, distribution,
):
    http, maker, csrf, _legacy_plan, sdk = workflow
    dsid, content = import_source(http, maker, csrf, tmp_path, source, sdk)
    with maker.begin() as db:
        people = list(db.scalars(select(Person).order_by(Person.id)))
        for person in people:
            person.check_in_at = 1
        team_id, person_id = people[0].team_id, people[0].id
    if distribution != "manual":
        if distribution == "balanced_30_v1":
            for species, group in (("SpeciesA", "General perching birds"),
                                   ("SpeciesB", "Surface-swimming waterfowl")):
                classified = http.put(f"/api/v1/coverage/datasets/{dsid}/classification",
                    json={"source_folder": species, "ecomorphology": group},
                    headers={"X-CSRF-Token": csrf})
                assert classified.status_code == 200, classified.text
            prepared = native.call(http, csrf, f"/datasets/{dsid}/balanced-preparation")
            assert prepared.status_code == 202, prepared.text
            with maker() as db:
                assert process_preparation_once(db)["status"] == "done"
        planned = native.call(http, csrf, "/labeling/plans", {
            "dataset_id": dsid, "mode": distribution})
        assert planned.status_code == 201, planned.text
        plan_id = planned.json()["plan_id"]
        assert native.call(http, csrf, f"/labeling/plans/{plan_id}/approve").status_code == 200
        # Local indexing alone must not release an automatic job.
        assert native.call(http, csrf, "/native/launch", {"plan_id": plan_id}).status_code == 409
    with maker() as db:
        assert process_mirror_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
    source_uploads = len(sdk.upload_history)
    if distribution == "manual":
        # Two independent manual assignments of the same block must never share
        # native image IDs or annotations, even though local source IDs coincide.
        created = native.call(http, csrf, "/labeling/manual/jobs", {
            "dataset_id": dsid, "dispatch": "publish", "allow_duplicates": True,
            "assignments": [{"team_id": person.team_id, "assignee_person_id": person.id,
                             "video_group": "SpeciesB/clip", "block_ordinal": 1}
                            for person in people]})
        assert created.status_code == 201, created.text
        job_ids = created.json()["job_ids"]
    else:
        started = native.call(http, csrf, "/native/launch", {"plan_id": plan_id})
        assert started.status_code == 200, started.text
        with maker.begin() as db:
            job = claim_primary_block(db, plan_id, team_id, person_id)
            assert job is not None and job.frame_count == 30
            job_ids = [job.id]
            assert claim_primary_block(db, plan_id, team_id, person_id) is None
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        for _job_id in job_ids:
            published = process_once(db, sdk, config)
            assert published["status"] == "done", published
        jobs = [db.get(LabelingJob, job_id) for job_id in job_ids]
        job_id, media_ids = jobs[0].id, list(jobs[0].media_ids)
        assert len(media_ids) == 30
        mappings = {row.media_id: row.native_image_id for row in db.scalars(
            select(NativeMediaInstance).where(NativeMediaInstance.job_id == job_id))}
        assert set(mappings) == set(media_ids)
        if distribution == "manual":
            second = {row.native_image_id for row in db.scalars(select(NativeMediaInstance).where(
                NativeMediaInstance.job_id == job_ids[1]))}
            assert second.isdisjoint(mappings.values())
            assert set(jobs[1].media_ids) == set(media_ids)
            assert len(sdk.upload_history) > source_uploads
        else:
            assert len(sdk.upload_history) == source_uploads
        native_id = db.scalar(select(NativeJobBinding.native_job_id).where(
            NativeJobBinding.job_id == job_id))
    remote = next(job for job in sdk.jobs if job.id == native_id)
    full_annotations = sdk.labeling_job.get_annotations
    missing_id = media_ids[-1]

    def partial_annotations(job_id, **kwargs):
        result = []
        for info, annotation in zip(kwargs["image_infos"], full_annotations(job_id, **kwargs)):
            value = annotation.to_json()
            if info.id == mappings[missing_id]:
                value["objects"] = value["objects"][:1]
            result.append(SimpleNamespace(to_json=lambda value=value: value))
        return result

    sdk.labeling_job.get_annotations = partial_annotations
    remote.status, remote.finished_images_count = "on_review", 29
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        sync_all(db, sdk, config, capture_snapshots=False)
        task = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "validate_submission", NativeOperation.target_id == job_id))
        assert task is not None and task.status == "pending"
        assert process_validation_once(db, sdk, config)["status"] == "done"
        job = db.get(LabelingJob, job_id)
        assert job.status == "needs_correction" and job.correction_round == 1
        accepted = list(db.scalars(select(ReviewSample).where(ReviewSample.job_id == job_id)))
        assert {sample.media_id for sample in accepted} == set(media_ids) - {missing_id}
        assert all(sample.validated_complete for sample in accepted)
        snapshots = {sample.id: dict(sample.snapshot) for sample in accepted}
        if distribution != "manual":
            assert db.get(WorkPacket, job_id).submitted_at is None
    claimed = native.call(http, csrf, "/review-queue/claim-next")
    assert claimed.status_code == 200, claimed.text
    review = claimed.json()["review"]
    assert review and review["source_media_id"] in media_ids
    preview = http.get(review["image_url"])
    assert preview.status_code == 200, preview.text
    assert preview.content == content
    source_preview = http.get(f"/api/v1/review/datasets/{dsid}/assets/{review['source_media_id']}/image")
    assert source_preview.status_code == 200 and source_preview.content == content
    verdict = {"claim_token": review["claim_token"], "verdicts": [
        {"object_index": i, "correct": True} for i in range(len(review["annotation"]["objects"]))]}
    completed = native.call(http, csrf, f"/review-queue/{review['review_id']}/complete", verdict)
    assert completed.status_code == 200, completed.text
    assert native.call(http, csrf, f"/review-queue/{review['review_id']}/complete", verdict).status_code == 200
    ranking = next(row for row in http.get("/api/v1/rankings/teams").json()["items"]
                   if row["team_id"] == team_id)
    expected = (0, 0, 0) if distribution == "manual" else (1, 1, 4)
    # Manual practice/repeat jobs remain reviewable but outside prize scoring.
    assert (ranking["reviewed_frames"], ranking["correct_boxes"], ranking["correct_keypoints"]) == expected
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        assert process_correction_once(db, sdk, config)["status"] == "done"
        binding = db.scalar(select(NativeJobBinding).where(NativeJobBinding.job_id == job_id))
        corrected = next(job for job in sdk.jobs if job.id == binding.native_job_id)
        assert corrected.image_ids == [mappings[missing_id]]
        assert corrected.id != native_id
    sdk.labeling_job.get_annotations = full_annotations
    corrected.status, corrected.finished_images_count = "on_review", 1
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        sync_all(db, sdk, config, capture_snapshots=False)
        assert process_validation_once(db, sdk, config)["status"] == "done"
        job = db.get(LabelingJob, job_id)
        assert job.status == "on_review" and job.annotation_snapshot_frames == 30
        accepted = list(db.scalars(select(ReviewSample).where(ReviewSample.job_id == job_id)))
        assert len(accepted) == 30 and {row.media_id for row in accepted} == set(media_ids)
        for sample in accepted:
            if sample.id in snapshots:
                assert sample.snapshot == snapshots[sample.id]
        assert db.get(ReviewSample, review["review_id"]).completed_at is not None
        dataset = db.get(MediaDataset, dsid)
        for sample in accepted:
            annotation = Path(dataset.root_path) / "annotations" / job_id / f"round-{sample.correction_round}" / f"{sample.media_id}.json"
            assert json.loads(annotation.read_text())["source_media_id"] == sample.media_id
        if distribution != "manual":
            assert db.get(WorkPacket, job_id).submitted_at is not None
            following = list(db.scalars(select(LabelingJob).where(
                LabelingJob.plan_id == plan_id, LabelingJob.id != job_id)))
            # Status polling may already have assigned the second checked-in
            # annotator. Completing the first job then releases the five-frame tail.
            assert sorted(row.frame_count for row in following) == [5, 30]
            assigned = [*media_ids, *(mid for row in following for mid in row.media_ids)]
            assert len(assigned) == len(set(assigned)) == 65
            assert len(sdk.upload_history) == source_uploads
        if source in {"mounted", "mounted_sync"}:
            assert native.call(http, csrf, f"/datasets/{dsid}/mounted/sync").status_code == 409
            assert not list((Path(dataset.root_path) / "media").rglob("*.jpg"))
            assert all(path.read_bytes() == content for path in (tmp_path / "sources").rglob("*.jpg"))
