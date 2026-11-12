"""Mocked official SDK contract: durable first release, recovery, pair status polling."""
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as Obj

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    CompetitionTeam,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    NativeJobBinding,
    NativeJobRevision,
    NativeMediaInstance,
    NativeOperation,
    NativeSourceMedia,
    Person,
    ReviewSample,
    StaffAccount,
    SuperviselyConfig,
    WorkPacket,
    epoch,
)
from annotatiathon.native_media_pool import enqueue_mirror
from annotatiathon.native_worker import (
    process_correction_once,
    process_event_reset,
    process_export_once,
    process_mirror_once,
    process_once,
    process_reassignment_once,
    sync_all,
)
from annotatiathon.security import password_hash


class MockSDK:
    """Only public SDK methods required by Nightjar are emulated."""

    def __init__(self):
        self.projects = []
        self.project_metas = {}
        self.meta_updates = []
        self.removed_projects = []
        self.archived_projects = []
        self.removed_datasets = []
        self.removed_jobs = []
        self.datasets = []
        self.images = {}
        self.jobs = []
        self.nextid = 100
        self.upload_history = []
        self.fail_job_create_once = False
        self.project = Obj(
            get_list=self.projects_list,
            get_list_all=self.projects_list_all,
            create=self.project_create,
            remove=self.project_remove,
            get_meta=self.projects_get_meta,
            update_meta=self.projects_update_meta,
            get_info_by_id=lambda ident: next(
                (p for p in self.projects + self.archived_projects
                 if p.id == ident), None
            ),
        )
        self.dataset = Obj(
            get_list=self.datasets_list,
            create=self.dataset_create,
            remove=self.dataset_remove,
            get_info_by_id=lambda ident: next(
                (d for d in self.datasets if d.id == ident), None
            ),
        )
        self.workspace = Obj(get_info_by_id=lambda ident: Obj(id=ident, team_id=125980))
        self.image = Obj(get_list=self.images_list, upload_paths=self.upload_paths)
        self.annotation = Obj(download_json=lambda image_id: {
            "native_image_id": image_id, "labels": [{"class": "bird"}],
            "size": {"width": 100, "height": 100},
            "objects": [{
                "classTitle": "bird", "geometryType": "rectangle",
                "points": {"exterior": [[12, 12], [82, 88]], "interior": []},
            }],
        })
        self.labeling_job = Obj(
            get_list=self.jobs_list,
            set_assignees=self.jobs_set_assignees,
            get_annotations=self.jobs_get_annotations,
            get_project_meta=self.jobs_get_project_meta,
            create=self.jobs_create,
            remove=self.jobs_remove,
            restart=self.jobs_restart,
            get_info_by_id=lambda ident: next((j for j in self.jobs if j.id == ident), None),
        )

    def new_id(self):
        self.nextid += 1
        return self.nextid

    def projects_list(self, workspace_id):
        return [p for p in self.projects if p.workspace_id == workspace_id]

    def projects_list_all(self, filters, skip_exported):
        assert filters[0]["field"] == "workspaceId"
        assert not skip_exported
        workspace_id = filters[0]["value"]
        return [p for p in self.projects + self.archived_projects
                if p.workspace_id == workspace_id]

    def project_remove(self, project_id):
        self.removed_projects.append(project_id)
        archived = next(p for p in self.projects if p.id == project_id)
        self.archived_projects.append(archived)
        self.projects = [p for p in self.projects if p.id != project_id]

    def projects_get_meta(self, project_id):
        return json.loads(json.dumps(self.project_metas[project_id]))

    def projects_update_meta(self, project_id, metadata):
        self.meta_updates.append(project_id)
        self.project_metas[project_id] = json.loads(json.dumps(metadata))
        return metadata

    def project_create(self, workspace_id, name, **kwargs):
        assert kwargs["change_name_if_conflict"] is False
        p = Obj(id=self.new_id(), workspace_id=workspace_id, name=name)
        self.projects.append(p)
        self.project_metas[p.id] = {
            "projectType": "images", "classes": [], "tags": []
        }
        return p

    def datasets_list(self, project_id):
        return [d for d in self.datasets if d.project_id == project_id]

    def dataset_remove(self, dataset_id):
        self.removed_datasets.append(dataset_id)
        self.datasets = [d for d in self.datasets if d.id != dataset_id]
        self.images.pop(dataset_id, None)

    def dataset_create(self, project_id, name, **kwargs):
        assert kwargs["change_name_if_conflict"] is False
        item = Obj(id=self.new_id(), project_id=project_id, name=name)
        self.datasets.append(item)
        return item

    def images_list(self, dataset_id, filters=None):
        images = list(self.images.get(dataset_id, []))
        if filters is None:
            return images
        assert len(filters) == 1
        assert filters[0]["field"] == "id"
        assert filters[0]["operator"] == "in"
        requested = set(filters[0]["value"])
        # Simulate an API response in a different order than the job frames.
        return [info for info in reversed(images) if info.id in requested]

    def upload_paths(self, dataset_id, names, paths, **kwargs):
        self.upload_history.append((dataset_id, list(names)))
        assert kwargs["conflict_resolution"] == "skip"
        assert len(names) == len(paths)
        for path in paths:
            assert Path(path).is_file()
        collection = self.images.setdefault(dataset_id, [])
        for name in names:
            if not any(item.name == name for item in collection):
                collection.append(Obj(id=self.new_id(), name=name))
        return [next(i for i in collection if i.name == name) for name in names]

    def jobs_list(self, team_id, dataset_id=None, project_id=None, **kwargs):
        assert team_id == 125980 and kwargs["show_disabled"] is True
        assert dataset_id is not None or project_id is not None
        return [j for j in self.jobs if
                (dataset_id is None or j.dataset_id == dataset_id) and
                (project_id is None or j.project_id == project_id)]

    def jobs_get_project_meta(self, job_id):
        job = next(item for item in self.jobs if item.id == job_id)
        return self.project_metas[job.project_id]

    def jobs_get_annotations(self, job_id, image_ids=None, project_meta=None,
                             image_infos=None):
        job = next(item for item in self.jobs if item.id == job_id)
        assert image_ids is None and image_infos is not None
        ids = [info.id for info in image_infos]
        assert set(ids).issubset(set(job.image_ids))
        assert project_meta == self.project_metas[job.project_id]

        def annotation_for(image_id):
            def to_json():
                return {
                    "name": str(image_id) + ".jpg",
                    "size": {"width": 100, "height": 100},
                    "objects": [{
                        "classTitle": "bird", "geometryType": "rectangle",
                        "points": {"exterior": [[12, 12], [82, 88]], "interior": []},
                        "tags": [{"job_id": job_id, "image_id": image_id}],
                    }] + [
                        {
                            "classTitle": cls, "geometryType": "point",
                            "points": {"exterior": [[20 + index, 20 + index]], "interior": []},
                        }
                        for index, cls in enumerate(
                            ("left_eye", "right_eye", "beak", "crown")
                        )
                    ],
                    "tags": [],
                }
            return Obj(to_json=to_json)

        return [annotation_for(image_id) for image_id in ids]

    def jobs_set_assignees(self, job_id, annotator_id):
        job = next(item for item in self.jobs if item.id == job_id)
        job.assigned_to_id = annotator_id
        return job

    def jobs_remove(self, job_id):
        self.removed_jobs.append(job_id)
        self.jobs = [j for j in self.jobs if j.id != job_id]

    def jobs_create(self, name, dataset_id, user_ids, images_ids,
                    classes_to_label, readme):
        assert images_ids
        assert set(images_ids).issubset({item.id for item in self.images[dataset_id]})
        assert classes_to_label == [
            "bird", "left_eye", "left_eye_occ", "right_eye", "right_eye_occ",
            "beak", "beak_occ", "crown", "crown_occ",
        ]
        assert "bounding box" in readme and "occluded" in readme
        dataset = next(d for d in self.datasets if d.id == dataset_id)
        classes = {
            cls["title"]: cls
            for cls in self.project_metas[dataset.project_id]["classes"]
        }
        assert len(classes) == 9
        assert classes["bird"]["shape"] == "rectangle"
        for key in ("left_eye", "left_eye_occ", "right_eye", "right_eye_occ",
                    "beak", "beak_occ", "crown", "crown_occ"):
            assert classes[key]["shape"] == "point"
        assert "Nightjar video " not in name
        assert " · " in name
        new_jobs = []
        for uid in user_ids:
            j = Obj(
                id=self.new_id(), name=name, dataset_id=dataset_id,
                project_id=dataset.project_id,
                classes_to_label=list(classes_to_label),
                image_ids=list(images_ids),
                assigned_to_id=uid, status="pending",
                images_count=len(images_ids), finished_images_count=0,
            )
            self.jobs.append(j)
            new_jobs.append(j)
        if self.fail_job_create_once:
            self.fail_job_create_once = False
            raise ConnectionError("Simulated uncertain write after creating remote jobs")
        return new_jobs

    def jobs_restart(self, job_id, assignee_ids, title, complete_existing,
                     only_rejected_entities):
        assert complete_existing is True and only_rejected_entities is False
        old = next(j for j in self.jobs if j.id == job_id)
        assert assignee_ids == [old.assigned_to_id]
        new = Obj(
            id=self.new_id(), name=title, dataset_id=old.dataset_id,
            project_id=old.project_id,
            assigned_to_id=assignee_ids[0], status="pending",
            image_ids=list(old.image_ids),
            images_count=old.images_count, finished_images_count=0,
        )
        self.jobs.append(new)
        old.status = "completed"
        return [{"id": new.id, "userId": new.assigned_to_id}]


@pytest.fixture
def configured(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Official event"))
        staff = StaffAccount(
            email="organiser@example.org", name="Organiser", roles=["organiser"],
            password_hash=password_hash("SafeTestingPassword!"),
        )
        db.add(staff)
        db.flush()
        db.add(SuperviselyConfig(
            id=1, server_url="https://app.supervisely.com",
            team_id=125980, workspace_id=2001, verified=True,
            token_ciphertext="FAKE_TEST_CIPHERTEXT",
        ))
        team = CompetitionTeam(event_id=DEFAULT_EVENT_ID, name="Pair One")
        db.add(team)
        db.flush()
        for idx in range(2):
            db.add(Person(
                event_id=DEFAULT_EVENT_ID, name=f"Bird Expert {idx}",
                email=f"bird{idx}@example.org", kind="participant",
                team_id=team.id, membership_status="joined",
                supervisely_user_id=3001 + idx,
                supervisely_role="annotator", native_verified_at=epoch(),
            ))
        root = tmp_path / "dataset"
        (root / "media" / "images" / "sparrows").mkdir(parents=True)
        assets = []
        for idx in range(3):
            relative = f"images/sparrows/frame{idx}.jpg"
            (root / "media" / relative).write_bytes(b"tiny image bytes")
            asset = MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id="dataset01",
                relative_path=relative, morphology="sparrows", kind="image",
                size_bytes=16, sha256="a"*64,
            )
            assets.append(asset)
        dataset = MediaDataset(
            id="dataset01", event_id=DEFAULT_EVENT_ID, name="Birds",
            upload_kind="folder", status="ready", root_path=str(root),
            created_by=staff.id,
        )
        dataset.image_count = len(assets)
        db.add(dataset)
        db.add_all(assets)
        db.flush()
        plan = LabelingPlan(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            jobs_per_team=2, redundancy=1, status="approved",
            created_by=staff.id,
        )
        db.add(plan)
        db.flush()
        job1 = LabelingJob(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            plan_id=plan.id, team_id=team.id, sequence=1,
            media_ids=[a.id for a in assets[:2]], frame_count=2,
            video_count=0, morphology_counts={"sparrows":2},
        )
        job2 = LabelingJob(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            plan_id=plan.id, team_id=team.id, sequence=2,
            media_ids=[assets[2].id], frame_count=1,
            video_count=0, morphology_counts={"sparrows":1},
        )
        db.add_all([job1, job2])
        db.flush()
        plan_id = plan.id
    monkeypatch.setattr(
        "annotatiathon.native_export_api.get_settings",
        lambda: type("Settings", (), {"local_data_dir": str(tmp_path)})(),
    )
    monkeypatch.setattr(
        "annotatiathon.full_reset_api.get_settings",
        lambda: type("Settings", (), {"local_data_dir": str(tmp_path)})(),
    )
    monkeypatch.setattr(
        "annotatiathon.native_operations_api.decrypt_token",
        lambda ciphertext: "PLACEHOLDER_TEST_TOKEN",
    )
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as http:
        login = http.post("/api/v1/auth/login", json={
            "email": "organiser@example.org", "password": "SafeTestingPassword!",
        })
        assert login.status_code == 200
        sdk = MockSDK()
        with maker() as db:
            dataset = db.get(MediaDataset, "dataset01")
            enqueue_mirror(db, dataset)
            db.commit()
            result = process_mirror_once(
                db, sdk, db.get(SuperviselyConfig, 1),
            )
            assert result["status"] == "done", result
        yield http, maker, login.json()["csrf_token"], plan_id, sdk
    app.dependency_overrides.clear()
    engine.dispose()


def call(http, csrf, path, body=None):
    return http.post(
        "/api/v1"+path, json=body, headers={"X-CSRF-Token": csrf},
    )


def launch(http, csrf, plan_id):
    response = call(http, csrf, "/native/launch", {"plan_id": plan_id})
    assert response.status_code == 200, response.text
    assert response.json()["first_jobs"] == 1
    return response


def test_initial_release_and_next_after_both_submit(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    assert len(sdk.jobs) == 0
    assert launch(http, csrf, plan_id).json()["first_jobs"] == 1
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        published = process_once(db, sdk, config)
        assert published["status"] == "done"
        assert published["native_jobs"] == 2
        assert len(sdk.meta_updates) == 1
        native_classes = {
            cls["title"]: cls for cls in sdk.project_metas[sdk.projects[0].id]["classes"]
        }
        assert len(native_classes) == 9
        assert native_classes["bird"]["shape"] == "rectangle"
        assert native_classes["left_eye"]["shape"] == "point"
        assert native_classes["left_eye_occ"]["shape"] == "point"
        assert process_once(db, sdk, config) is None
        assert len(db.scalars(select(NativeJobBinding)).all()) == 2
        assert len(db.scalars(select(NativeMediaInstance)).all()) == 2
        assert len(sdk.datasets) == 1
        assert len(sdk.jobs) == 2
        assert sync_all(db, sdk, config)["changed_jobs"] == 0
        ops = list(db.scalars(select(NativeOperation).where(
            NativeOperation.kind == "publish_job",
        )))
        assert len(ops) == 1
        sdk.jobs[0].status = "on_review"
        sdk.jobs[0].finished_images_count = 2
        sync_all(db, sdk, config)
        assert len(db.scalars(select(NativeOperation).where(
            NativeOperation.kind != "mirror_dataset",
        )).all()) == 1
        sdk.jobs[1].status = "on_review"
        sdk.jobs[1].finished_images_count = 1
        assert sync_all(db, sdk, config)["changed_jobs"] == 0
        assert process_once(db, sdk, config) is None
        sdk.jobs[1].finished_images_count = 2
        result = sync_all(db, sdk, config)
        assert result["changed_jobs"] == 1
        assert len(db.scalars(select(NativeOperation).where(
            NativeOperation.kind != "mirror_dataset",
        )).all()) == 1
        # The second job must NOT be published before monitor acceptance.
        assert process_once(db, sdk, config) is None
        first_job = db.scalar(select(LabelingJob).where(
            LabelingJob.sequence == 1
        ))
        media_id = first_job.media_ids[0]
        first_id = first_job.id
    snapshot = http.get("/api/v1/review/jobs/"+first_id+"/annotations/"+media_id)
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["source_media_id"] == media_id
    assert snapshot.json()["annotation"]["labels"][0]["class"] == "bird"
    assert snapshot.json()["native_image_id"] in {
        image.id for image in sdk.images[sdk.datasets[0].id]
    }
    review = http.put("/api/v1/review/jobs/"+first_id+"/frames", json={
        "media_id": media_id, "verdict": "good", "correct_keypoints": 1,
    }, headers={"X-CSRF-Token": csrf})
    assert review.status_code == 200, review.text
    approved = call(http, csrf, "/review/jobs/"+first_id+"/decision", {
        "decision": "accept",
    })
    assert approved.status_code == 200, approved.text
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        sync_all(db, sdk, config)
        assert len(db.scalars(select(NativeOperation).where(
            NativeOperation.kind != "mirror_dataset",
        )).all()) == 2
        next_run = process_once(db, sdk, config)
        assert next_run["status"] == "done"
        assert len(sdk.datasets) == 1
        assert len(sdk.jobs) == 4
        assert len(sdk.meta_updates) == 1  # No redundant metadata write per job.
        # One shared remote dataset, distinct video-frame subsets per job.
        assert len(sdk.datasets) == 1
        first_video_ids = set(sdk.jobs[0].image_ids)
        second_video_ids = set(sdk.jobs[2].image_ids)
        assert first_video_ids.isdisjoint(second_video_ids)
        assert first_video_ids | second_video_ids == {
            row.id for row in sdk.images[sdk.datasets[0].id]
        }
    overview = http.get("/api/v1/native/overview").json()
    assert overview["native_bindings"] == 4
    assert len([item for item in overview["operations"]
                if item["kind"] == "publish_job"]) == 2


def test_returned_pair_job_restarts_and_blocks_next_release(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, config)["status"] == "done"
        job = db.scalar(select(LabelingJob).where(LabelingJob.sequence == 1))
        jid, mid = job.id, job.media_ids[0]
        for native in sdk.jobs:
            native.status = "on_review"
            native.finished_images_count = native.images_count
        assert sync_all(db, sdk, config)["changed_jobs"] == 1
        assert process_once(db, sdk, config) is None
    review = http.put("/api/v1/review/jobs/"+jid+"/frames", json={
        "media_id": mid, "verdict": "bad", "missing_keypoints": 2,
    }, headers={"X-CSRF-Token": csrf})
    assert review.status_code == 200
    returned = call(http, csrf, "/review/jobs/"+jid+"/decision", {
        "decision": "return_for_correction", "notes": "Incomplete head keypoints",
    })
    assert returned.status_code == 200, returned.text
    assert returned.json()["native_correction_queued"] is True
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        assert sync_all(db, sdk, config)["pending_operations"] == 0
        assert process_once(db, sdk, config) is None
        corrected = process_correction_once(db, sdk, config)
        assert corrected["status"] == "done", corrected
        assert len(db.scalars(select(NativeJobRevision)).all()) == 2
        assert len(sdk.jobs) == 4
        first_job = db.get(LabelingJob, jid)
        assert first_job.status == "in_progress"
        assert first_job.correction_round == 1
        active_ids = [
            x.native_job_id for x in db.scalars(select(NativeJobBinding).where(
                NativeJobBinding.job_id == jid
            ))
        ]
        for native in sdk.jobs:
            if native.id in active_ids:
                native.status = "on_review"
                native.finished_images_count = native.images_count
        assert sync_all(db, sdk, config)["changed_jobs"] == 1
    fixed = http.put("/api/v1/review/jobs/"+jid+"/frames", json={
        "media_id": mid, "verdict": "good", "correct_keypoints": 3,
    }, headers={"X-CSRF-Token": csrf})
    assert fixed.status_code == 200
    approved = call(http, csrf, "/review/jobs/"+jid+"/decision", {
        "decision": "accept",
    })
    assert approved.status_code == 200, approved.text
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        sync_all(db, sdk, config)
        assert len(db.scalars(select(NativeOperation).where(
            NativeOperation.kind != "mirror_dataset",
        )).all()) == 3
        assert process_once(db, sdk, config)["status"] == "done"




def test_uncertain_native_create_is_reconciled_without_duplicates(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    sdk.fail_job_create_once = True
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        result = process_once(db, sdk, config)
        assert result["status"] == "attention"
        assert len(sdk.projects) == 1 and len(sdk.datasets) == 1
        assert len(sdk.jobs) == 2
        op = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "publish_job",
        ))
        opid = op.id
        assert op.status == "attention"
    retry = call(http, csrf, "/native/operations/"+opid+"/retry")
    assert retry.status_code == 200, retry.text
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        assert len(sdk.jobs) == 2
        assert len(sdk.images[sdk.datasets[0].id]) == 3
        assert len(db.scalars(select(NativeJobBinding)).all()) == 2


def test_pause_blocks_releasing_new_native_jobs(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    response = call(http, csrf, "/native/plans/"+plan_id+"/pause")
    assert response.status_code == 200
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1)) is None
        assert len(sdk.projects) == 1
    resume = call(http, csrf, "/native/plans/"+plan_id+"/resume")
    assert resume.status_code == 200
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"



def test_read_only_native_export_preserves_source_and_curated_manifest(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, config)["status"] == "done"
        job = db.scalar(select(LabelingJob).where(LabelingJob.sequence == 1))
        job.status = "completed"
        job.review_status = "accepted"
        db.get(Event, DEFAULT_EVENT_ID).status = "archived"
        db.commit()
    prepare = call(http, csrf, "/native/export/prepare")
    assert prepare.status_code == 202, prepare.text
    with maker() as db:
        result = process_export_once(db, sdk)
        assert result["status"] == "done", result
        assert result["total_copies"] == 2
        assert result["curated_copies"] == 2
    status = http.get("/api/v1/native/export").json()
    assert status["ready_to_download"] is True
    response = http.get("/api/v1/native/export/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        paths = archive.namelist()
        full = [
            json.loads(row)
            for row in archive.read("full_manifest.jsonl").decode().splitlines()
        ]
        curated = [
            json.loads(row)
            for row in archive.read("curated_manifest.jsonl").decode().splitlines()
        ]
        assert len(full) == len(curated) == 2
        assert len({item["native_image_id"] for item in full}) == 2
        assert all(len(item["native_job_ids"]) == 2 for item in full)
        assert all(item["source_sha256"] == "a"*64 for item in full)
        assert len([name for name in paths if name.startswith("annotations/")]) == 2
        info = json.loads(archive.read("README.json"))
        assert info["annotation_state"] == "current_snapshot_only"
    again = call(http, csrf, "/native/export/prepare")
    assert again.status_code == 202
    assert again.json()["status"] == "done"
    assert len(sdk.jobs) == 2


def test_manual_assignments_publish_independent_videos(configured):
    http, maker, csrf, _plan, sdk = configured
    with maker.begin() as db:
        ds = db.get(MediaDataset, "dataset01")
        people = list(db.scalars(select(Person).order_by(Person.id)))
        assignments = []
        for index, person in enumerate(people):
            group = f"videos/owls/video-{index}"
            relative = group + "/0001.jpg"
            file = Path(ds.root_path) / "media" / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"bird")
            db.add(MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=ds.id,
                relative_path=relative, morphology="owls",
                kind="video_frame", video_group=group, frame_index=1,
                size_bytes=4, sha256="a"*64,
            ))
            assignments.append({
                "team_id": person.team_id,
                "assignee_person_id": person.id, "video_group": group,
            })
    result = call(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": "dataset01", "dispatch": "publish",
        "assignments": assignments,
    })
    assert result.status_code == 201, result.text
    ids = result.json()["job_ids"]
    assert len(ids) == 2
    with maker() as db:
        conf = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, conf)["native_jobs"] == 1
        assert process_once(db, sdk, conf)["native_jobs"] == 1
        assert len(sdk.jobs) == 2
        assert len({j.dataset_id for j in sdk.jobs}) == 1
        assert sdk.jobs[0].dataset_id != sdk.datasets[0].id
        for native in sdk.jobs:
            native.status = "on_review"
            native.finished_images_count = 0
        assert sync_all(db, sdk, conf)["changed_jobs"] == 2
    for jobid in ids:
        r = http.get("/api/v1/review/jobs/" + jobid)
        assert r.status_code == 200
        assert r.json()["annotation_snapshot_ready"]
        with maker() as db:
            job = db.get(LabelingJob, jobid)
            dataset = db.get(MediaDataset, job.dataset_id)
            manifest = Path(dataset.root_path) / "annotations" / jobid / "round-0" / "manifest.json"
            assert json.loads(manifest.read_text(encoding="utf-8"))["annotated_frames"] == 1
            assert job.annotation_snapshot_round == 0
            # A media restore may have an older copy than the database counters.
            manifest.parent.rename(manifest.parent.with_name("round-0-before-restore"))
            from annotatiathon.native_worker import capture_job_annotations
            assert capture_job_annotations(db, sdk, job)
            assert json.loads(manifest.read_text(encoding="utf-8"))["annotated_frames"] == 1


def test_confirmed_reset_archives_only_event_projects_and_keeps_members(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        conf = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, conf)["status"] == "done"
        owned_project = sdk.projects[0].id
        other = sdk.project.create(2001, "Other research", change_name_if_conflict=False)
        assert len(sdk.jobs) == 2

    preview = http.get("/api/v1/settings/reset/preview")
    assert preview.status_code == 200, preview.text
    state = preview.json()
    assert state["jobs"] == 2
    assert state["preserved_people"] == 2
    assert state["preserved_teams"] == 1
    wrong = call(http, csrf, "/settings/reset/request", {"confirmation": "WRONG"})
    assert wrong.status_code == 422
    requested = call(http, csrf, "/settings/reset/request", {
        "confirmation": state["confirmation_phrase"],
    })
    assert requested.status_code == 202, requested.text
    with maker() as db:
        assert db.get(Event, DEFAULT_EVENT_ID).status == "resetting"
        conf = db.get(SuperviselyConfig, 1)
        result = process_event_reset(db, sdk, conf)
        assert result["status"] == "done", result
        assert db.get(Event, DEFAULT_EVENT_ID).status == "active"
        assert not list(db.scalars(select(MediaDataset)))
        assert not list(db.scalars(select(LabelingJob)))
        assert len(list(db.scalars(select(Person)))) == 2
        assert len(list(db.scalars(select(CompetitionTeam)))) == 1
        assert db.get(SuperviselyConfig, 1).team_id == 125980
    assert owned_project in sdk.removed_projects
    assert len(sdk.removed_jobs) == 2
    assert len(sdk.removed_datasets) == 1
    assert not sdk.jobs
    assert not any(d.project_id == owned_project for d in sdk.datasets)
    assert other.id not in sdk.removed_projects
    assert len(sdk.projects) == 1
    assert sdk.projects[0].id == other.id
    final = http.get("/api/v1/settings/reset/preview")
    assert final.status_code == 200
    assert final.json()["datasets"] == 0
    assert final.json()["jobs"] == 0
    assert final.json()["operation"]["status"] == "done"
    assert final.json()["remote_action"] == "remove_labeling_jobs_archive_datasets_and_projects"
    categories = http.get("/api/v1/tickets/categories")
    assert categories.status_code == 200
    assert len(categories.json().get("items", [])) >= 3


def test_team_dashboard_rankings_and_monitor_scoped_review(configured):
    from annotatiathon.models import JobReview, MonitorAssignment

    http, maker, _csrf, _plan, _sdk = configured
    with maker.begin() as db:
        original_team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID))
        other_team = CompetitionTeam(
            event_id=DEFAULT_EVENT_ID, name="Second Pair")
        db.add(other_team)
        db.flush()
        admin = db.scalar(select(StaffAccount).where(
            StaffAccount.email == "organiser@example.org"
        ))
        monitor = Person(
            event_id=DEFAULT_EVENT_ID, name="Review Monitor",
            email="review-monitor@example.org", kind="monitor",
        )
        db.add(monitor)
        staff = StaffAccount(
            email=monitor.email, name=monitor.name,
            roles=["monitor"], password_hash=password_hash("MonitorPassword123!"),
        )
        db.add(staff)
        db.flush()
        db.add(MonitorAssignment(
            event_id=DEFAULT_EVENT_ID, team_id=original_team.id,
            monitor_person_id=monitor.id, assigned_by=admin.id,
        ))
        jobs = list(db.scalars(select(LabelingJob).order_by(LabelingJob.sequence)))
        job = jobs[0]
        job.started_at = 1000
        job.submitted_at = 1100
        job.status = "completed"
        job.review_status = "accepted"
        job.completed_at = 1200
        db.add(JobReview(
            event_id=DEFAULT_EVENT_ID, job_id=job.id, media_id=job.media_ids[0],
            verdict="good", correct_keypoints=3, missing_keypoints=1,
            incorrect_keypoints=1, visible_keypoints=3, occluded_keypoints=0,
            reviewer_id=admin.id,
        ))
        outside = LabelingJob(
            event_id=DEFAULT_EVENT_ID, plan_id=job.plan_id,
            dataset_id=job.dataset_id, team_id=other_team.id, sequence=1,
            media_ids=[job.media_ids[0]], frame_count=1, video_count=0,
            morphology_counts={"sparrows": 1},
        )
        db.add(outside)
        db.flush()
        outside_id = outside.id
        own_id = job.id
        team_id = original_team.id

    summary = http.get("/api/v1/review/team-metrics")
    assert summary.status_code == 200
    ranked = summary.json()["teams"]
    assert len(ranked) == 2
    assert ranked[0]["team_id"] == team_id
    assert ranked[0]["keypoint_accuracy_pct"] == 60.0
    assert ranked[0]["average_seconds_per_frame"] == 50.0
    assert ranked[0]["jobs_completed"] == 1
    assert ranked[1]["rank"] is None

    with TestClient(app) as monitor_client:
        login = monitor_client.post("/api/v1/auth/login", json={
            "email": "review-monitor@example.org",
            "password": "MonitorPassword123!",
        })
        assert login.status_code == 200, login.text
        allowed = monitor_client.get("/api/v1/review/assigned-jobs")
        assert allowed.status_code == 200
        assert {j["team_id"] for j in allowed.json()["items"]} == {
            team_id, other_team.id,
        }
        assert monitor_client.get("/api/v1/review/team-metrics").status_code == 200
        # Monitors can inspect other competition teams, but cannot mutate the
        # old whole-job review flow without authorization.
        assert monitor_client.get("/api/v1/review/jobs/" + outside_id).status_code == 200
        assert monitor_client.get("/api/v1/review/jobs/" + outside_id +
                                  "/annotations/test-frame").status_code == 404
        assert monitor_client.get("/api/v1/labeling/jobs/" + outside_id + "/media").status_code == 200
        assert monitor_client.get("/api/v1/review/jobs/" + own_id).status_code == 200


def test_private_roster_csv_fields_are_restricted_and_editable(configured):
    from annotatiathon.people_api import private_person_fields

    http, maker, csrf, _plan, _sdk = configured
    with maker.begin() as db:
        person = db.scalar(select(Person).where(Person.kind == "participant"))
        person.source_fields = {
            "Data de nascimento": "2008-02-01",
            "Telefone": "900000000",
            "Menores de 18 anos": "Sim",
            "Autorização de cedência de   imagem": "Sim",
            "Autorização de tratamento de   dados": "Não",
            "Alergias/Intolerâncias   alimentares": "Lactose",
        }
        pid = person.id
    with maker() as db:
        values = private_person_fields(db.get(Person, pid))
        assert values["date_of_birth"] == "2008-02-01"
        assert values["image_consent"] == "Sim"
        assert values["data_consent"] == "Não"
        assert values["food_allergies"] == "Lactose"
    roster = http.get("/api/v1/people")
    assert roster.status_code == 200
    assert any(p["phone"] == "900000000" for p in roster.json()["items"])
    patch = http.patch("/api/v1/people/" + pid, json={
        "food_allergies": "None reported",
    }, headers={"X-CSRF-Token": csrf})
    assert patch.status_code == 200, patch.text
    assert patch.json()["food_allergies"] == "None reported"
    with maker.begin() as db:
        db.add(StaffAccount(
            email="food@example.org", name="Food Distribution",
            roles=["distribution"], password_hash=password_hash("FoodPassword123!"),
        ))
    with TestClient(app) as food_client:
        login = food_client.post("/api/v1/auth/login", json={
            "email": "food@example.org", "password": "FoodPassword123!",
        })
        assert login.status_code == 200
        roster_food = food_client.get("/api/v1/people")
        assert roster_food.status_code == 200
        food_person = next(p for p in roster_food.json()["items"] if p["id"] == pid)
        assert food_person["food_allergies"] == "None reported"
        assert "date_of_birth" not in food_person
        assert "data_consent" not in food_person
        assert "phone" not in food_person


def test_reset_cleans_historical_jobs_from_already_archived_project(configured):
    """A previous archive-only reset could leave jobs without local bindings."""
    from sqlalchemy import delete

    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        conf = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, conf)["status"] == "done"
        owned_id = sdk.projects[0].id
        assert len(sdk.jobs) == 2
        sdk.project.remove(owned_id)
    with maker.begin() as db:
        db.execute(delete(NativeJobBinding))
    assert owned_id not in {p.id for p in sdk.projects}
    assert owned_id in {p.id for p in sdk.archived_projects}
    preview = http.get("/api/v1/settings/reset/preview").json()
    queued = call(http, csrf, "/settings/reset/request", {
        "confirmation": preview["confirmation_phrase"],
    })
    assert queued.status_code == 202, queued.text
    with maker() as db:
        result = process_event_reset(db, sdk, db.get(SuperviselyConfig, 1))
        assert result["status"] == "done", result
        assert result["removed_native_jobs"] == 2
        assert result["archived_projects"] == 0
        assert db.get(Event, DEFAULT_EVENT_ID).status == "active"
    assert sdk.jobs == []
    assert sdk.removed_jobs and len(sdk.removed_jobs) == 2


def test_reset_can_retry_after_remote_job_removal_interruption(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
    preview = http.get("/api/v1/settings/reset/preview").json()
    queued = call(http, csrf, "/settings/reset/request", {
        "confirmation": preview["confirmation_phrase"],
    })
    assert queued.status_code == 202, queued.text
    original_remove = sdk.labeling_job.remove
    failed_once = False

    def interrupted_remove(job_id):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            original_remove(job_id)
            raise ConnectionError("Simulated lost response after removal")
        original_remove(job_id)

    sdk.labeling_job.remove = interrupted_remove
    with maker() as db:
        first = process_event_reset(db, sdk, db.get(SuperviselyConfig, 1))
        assert first["status"] == "attention"
        assert db.get(Event, DEFAULT_EVENT_ID).status == "resetting"
        assert list(db.scalars(select(MediaDataset)))
        assert list(db.scalars(select(LabelingJob)))
    sdk.labeling_job.remove = original_remove
    queued = call(http, csrf, "/settings/reset/retry")
    assert queued.status_code == 202, queued.text
    with maker() as db:
        second = process_event_reset(db, sdk, db.get(SuperviselyConfig, 1))
        assert second["status"] == "done", second
        assert db.get(Event, DEFAULT_EVENT_ID).status == "active"
        assert not list(db.scalars(select(MediaDataset)))
        assert not list(db.scalars(select(LabelingJob)))
    assert not sdk.jobs


def test_pre_event_mirror_is_complete_and_job_publish_uploads_zero_frames(configured):
    """Production path: all frames preloaded once, job uses a selected subset."""
    http, maker, csrf, plan_id, sdk = configured
    assert len(sdk.upload_history) == 1
    with maker() as db:
        assert len(list(db.scalars(select(NativeSourceMedia)))) == 3
        source_ds = db.get(MediaDataset, "dataset01")
        assert source_ds.native_dataset_id == sdk.datasets[0].id
    preview = http.get("/api/v1/datasets/dataset01/mirror-status")
    assert preview.status_code == 200, preview.text
    assert preview.json()["ready_for_automatic_jobs"] is True
    launch(http, csrf, plan_id)
    with maker() as db:
        result = process_once(db, sdk, db.get(SuperviselyConfig, 1))
        assert result["status"] == "done", result
        job = db.scalar(select(LabelingJob).where(LabelingJob.sequence == 1))
        mapped = list(db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.job_id == job.id,
        )))
        assert len(mapped) == 2
        assert all(m.native_dataset_id == source_ds.native_dataset_id for m in mapped)
    # Publishing to Supervisely creates only labeling-job records, never uploads.
    assert len(sdk.upload_history) == 1
    assert len(sdk.datasets) == 1
    assert len(sdk.images[sdk.datasets[0].id]) == 3


@pytest.mark.parametrize("repeat", [False, True])
def test_manual_duplicate_jobs_use_independent_copies_and_annotator_mappings(configured, repeat):
    from annotatiathon.models import WorkPacket

    http, maker, csrf, _plan_id, sdk = configured
    with maker.begin() as db:
        ds = db.get(MediaDataset, "dataset01")
        source = Path(ds.root_path) / "media" / "videos" / "sparrows" / "flight01"
        source.mkdir(parents=True, exist_ok=True)
        (source / "0001.jpg").write_bytes(b"one video frame")
        asset = MediaAsset(
            event_id=DEFAULT_EVENT_ID, dataset_id=ds.id,
            relative_path="videos/sparrows/flight01/0001.jpg",
            morphology="sparrows", kind="video_frame", video_group="sparrows/flight01",
            size_bytes=15, sha256="a" * 64,
        )
        db.add(asset)
        db.flush()
        members = list(db.scalars(select(Person).where(Person.kind == "participant")))
        team_id = members[0].team_id
        asset_id = asset.id
    response = call(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": "dataset01", "dispatch": "publish",
        "allow_duplicates": True,
        "high_priority": True,
        "assignments": [
            {"team_id": team_id, "assignee_person_id": members[0].id,
             "video_group": "sparrows/flight01"},
            {"team_id": team_id, "assignee_person_id": members[1].id,
             "video_group": "sparrows/flight01"},
        ],
    })
    assert response.status_code == 201, response.text
    assert response.json()["priority"] == "higher"
    with maker() as db:
        ops = list(db.scalars(select(NativeOperation).where(
            NativeOperation.kind == "publish_job",
            NativeOperation.target_id.in_(response.json()["job_ids"]),
        )))
        if repeat:
            job = db.get(LabelingJob, response.json()["job_ids"][1])
            job.release_mode = "shared_repeat"
            db.add(WorkPacket(job_id=job.id, event_id=job.event_id, person_id=job.assignee_person_id))
            db.commit()
        assert len(ops) == 2
        assert all(op.priority == 100 for op in ops)
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        ds = db.get(MediaDataset, "dataset01")
        assert ds.native_manual_dataset_id is not None
        assert ds.native_manual_dataset_id != ds.native_dataset_id
        maps = list(db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.media_id == asset_id,
        )))
        assert len(maps) == 2
        assert len({x.native_image_id for x in maps}) == 2
        assert {x.native_dataset_id for x in maps} == {ds.native_manual_dataset_id}
    assert len(sdk.jobs) == 2
    assert len(sdk.datasets) == 2
    assert len(sdk.upload_history) == 3  # source upload + two manual copies
    for native_job in sdk.jobs:
        native_job.status = "on_review"
        native_job.finished_images_count = native_job.images_count
    with maker() as db:
        result = sync_all(db, sdk, db.get(SuperviselyConfig, 1))
        assert result["changed_jobs"] == 2
        if repeat:
            assert db.get(WorkPacket, response.json()["job_ids"][1]).submitted_at is not None
        dataset = db.get(MediaDataset, "dataset01")
        original_path = (Path(dataset.root_path) / "media" /
                         "videos/sparrows/flight01/0001.jpg")
        assert original_path.read_bytes() == b"one video frame"
        for job_id in response.json()["job_ids"]:
            native_id = db.scalar(select(NativeJobBinding.native_job_id).where(
                NativeJobBinding.job_id == job_id,
            ))
            snapshot = (Path(dataset.root_path) / "annotations" / job_id /
                        "round-0" / (asset_id + ".json"))
            assert snapshot.is_file()
            data = json.loads(snapshot.read_text(encoding="utf-8"))
            assert data["source_media_id"] == asset_id
            assert data["native_job_id"] == native_id
            assert data["annotation"]["objects"][0]["tags"][0]["job_id"] == native_id
            assert not (snapshot.parent / "images").exists()
            assert not (snapshot.parent / "videos").exists()



def test_submitted_job_json_matches_source_frames_even_if_api_reorders(configured):
    """One JSON per original frame; no images downloaded or frame mix-ups."""
    http, maker, csrf, _plan, sdk = configured
    with maker.begin() as db:
        dataset = db.get(MediaDataset, "dataset01")
        member = db.scalar(select(Person).where(Person.kind == "participant"))
        video = "videos/sparrows/order-test"
        for index in (1, 2):
            relative = f"{video}/{index:04d}.jpg"
            source = Path(dataset.root_path) / "media" / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"bird frame")
            db.add(MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
                relative_path=relative, morphology="sparrows",
                kind="video_frame", video_group=video,
                frame_index=index, size_bytes=10, sha256="a" * 64,
            ))
        person_id, team_id = member.id, member.team_id
    result = call(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": "dataset01", "dispatch": "publish",
        "assignments": [{"team_id": team_id, "assignee_person_id": person_id,
                         "video_group": video}],
    })
    assert result.status_code == 201, result.text
    job_id = result.json()["job_ids"][0]
    with maker() as db:
        conf = db.get(SuperviselyConfig, 1)
        assert process_once(db, sdk, conf)["status"] == "done"
        local_ids = list(db.get(LabelingJob, job_id).media_ids)
        mapping = {
            item.media_id: item.native_image_id
            for item in db.scalars(select(NativeMediaInstance).where(
                NativeMediaInstance.job_id == job_id,
            ))
        }
    assert len(local_ids) == 2 and len(mapping) == 2
    sdk.jobs[-1].status = "on_review"
    with maker() as db:
        synced = sync_all(db, sdk, db.get(SuperviselyConfig, 1))
        assert synced["annotation_snapshots"] == 1, synced
        assert synced["annotation_sync_errors"] == 0, synced
    for media_id in local_ids:
        response = http.get(f"/api/v1/review/jobs/{job_id}/annotations/{media_id}")
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["native_image_id"] == mapping[media_id]
        assert snapshot["annotation"]["objects"][0]["tags"][0]["image_id"] == mapping[media_id]


def test_live_reassignment_updates_supervisely_before_local_team(configured):
    http, maker, csrf, _plan_id, sdk = configured
    with maker.begin() as db:
        origin = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
        ))
        team = CompetitionTeam(event_id=DEFAULT_EVENT_ID, name="Replacement Pair")
        db.add(team)
        db.flush()
        replacement = Person(
            event_id=DEFAULT_EVENT_ID, name="Replacement Annotator",
            email="replacement@example.org", kind="participant",
            team_id=team.id, membership_status="joined", active=True,
            supervisely_user_id=4040, supervisely_role="annotator",
            native_verified_at=epoch(),
        )
        db.add(replacement)
        source = Path(db.get(MediaDataset, "dataset01").root_path) / "media" / "videos" / "sparrows"
        source.mkdir(parents=True, exist_ok=True)
        (source / "new.jpg").write_bytes(b"new test frame")
        db.add(MediaAsset(
            event_id=DEFAULT_EVENT_ID, dataset_id="dataset01",
            relative_path="videos/sparrows/new.jpg",
            morphology="sparrows", kind="video_frame",
            video_group="sparrows/flight02",
            size_bytes=14, sha256="b" * 64,
        ))
        origin_member = db.scalar(select(Person).where(Person.team_id == origin.id))
        origin_id, target_id = origin.id, replacement.id
    created = call(http, csrf, "/labeling/manual/jobs", {
        "dataset_id": "dataset01", "dispatch": "publish",
        "assignments": [{
            "team_id": origin_id, "assignee_person_id": origin_member.id,
            "video_group": "sparrows/flight02",
        }],
    })
    assert created.status_code == 201, created.text
    job_id = created.json()["job_ids"][0]
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        native_id = db.scalar(select(NativeJobBinding.native_job_id).where(
            NativeJobBinding.job_id == job_id,
        ))
        assert sdk.labeling_job.get_info_by_id(native_id).assigned_to_id == 3001
    requested = call(http, csrf, "/labeling/manual/jobs/"+job_id+"/reassign", {
        "person_id": target_id,
    })
    assert requested.status_code == 202, requested.text
    with maker() as db:
        job = db.get(LabelingJob, job_id)
        assert job.team_id == origin_id
        assert job.assignee_person_id == origin_member.id
        assert process_reassignment_once(
            db, sdk, db.get(SuperviselyConfig, 1),
        )["status"] == "done"
        job = db.get(LabelingJob, job_id)
        assert job.team_id == team.id
        assert job.assignee_person_id == target_id
        binding = db.scalar(select(NativeJobBinding).where(
            NativeJobBinding.job_id == job_id,
        ))
        assert binding.person_id == target_id
        assert sdk.labeling_job.get_info_by_id(native_id).assigned_to_id == 4040
    assert len(sdk.jobs) == 1  # no new duplicate native job


def test_queue_priority_prefers_manual_high_priority(configured):
    from annotatiathon.native_operations_api import queue_publish

    _http, maker, _csrf, _plan_id, sdk = configured
    with maker.begin() as db:
        jobs = list(db.scalars(select(LabelingJob).order_by(LabelingJob.sequence)))
        for job in jobs:
            job.assignee_person_id = db.scalar(select(Person.id).where(
                Person.team_id == job.team_id
            ))
            job.release_mode = "manual_released"
            db.get(LabelingPlan, job.plan_id).status = "running"
        low = queue_publish(db, jobs[0])
        high = queue_publish(db, jobs[1], high_priority=True)
        assert low.priority == 0 and high.priority == 100
        higher_id = jobs[1].id
    with maker() as db:
        done = process_once(db, sdk, db.get(SuperviselyConfig, 1))
        assert done["status"] == "done"
        op = db.get(NativeOperation, done["operation_id"])
        assert op.target_id == higher_id


def test_partial_submission_accepts_frames_once_and_reissues_only_missing(configured):
    """Partial native retries never create new logical competition jobs."""
    from annotatiathon.native_operations_api import queue_publish

    _http, maker, _csrf, _plan_id, sdk = configured
    with maker.begin() as db:
        job = db.scalar(select(LabelingJob).where(LabelingJob.sequence == 1))
        member = db.scalar(select(Person).where(Person.team_id == job.team_id))
        job.assignee_person_id = member.id
        job.release_mode = "shared_primary"
        db.get(LabelingPlan, job.plan_id).status = "running"
        db.add(WorkPacket(event_id=job.event_id, job_id=job.id,
                          person_id=member.id))
        db.flush()
        queue_publish(db, job)
        job_id, source_ids = job.id, list(job.media_ids)
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        mapped = {m.media_id: m.native_image_id for m in db.scalars(
            select(NativeMediaInstance).where(NativeMediaInstance.job_id == job_id)
        )}
        assert set(mapped) == set(source_ids)
    original_native = sdk.jobs[-1]
    first_native_id = original_native.id
    full_annotations = sdk.labeling_job.get_annotations

    def partial_annotations(job_id, **kwargs):
        annotations = full_annotations(job_id, **kwargs)
        result = []
        for image_info, annotation in zip(kwargs["image_infos"], annotations):
            data = annotation.to_json()
            if image_info.id == mapped[source_ids[1]]:
                data["objects"] = data["objects"][:1]
            result.append(Obj(to_json=lambda value=data: value))
        return result

    sdk.labeling_job.get_annotations = partial_annotations

    def forbidden_restart(*args, **kwargs):
        raise AssertionError("SDK restart must never be invoked for single-user subsets")

    sdk.labeling_job.restart = forbidden_restart
    original_native.status = "on_review"
    original_native.finished_images_count = 1
    with maker() as db:
        assert sync_all(db, sdk, db.get(SuperviselyConfig, 1))["changed_jobs"] == 1
        job = db.get(LabelingJob, job_id)
        assert job.status == "needs_correction"
        assert job.correction_round == 1
        assert job.review_status == "returned_incomplete"
        assert db.get(WorkPacket, job_id).submitted_at is None
        accepted = list(db.scalars(select(ReviewSample).where(
            ReviewSample.job_id == job_id
        )))
        assert len(accepted) == 1
        assert accepted[0].media_id == source_ids[0]
        assert accepted[0].validated_complete
        assert accepted[0].completed_at is None
        operation = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "restart_job",
            NativeOperation.target_id == job_id,
        ))
        assert operation.status == "pending"
        assert operation.payload["pending_media_ids"] == source_ids[1:]
        assert operation.payload["source_round"] == 0

        # Accepted frame JSON is retained for correction tracking, but
        # monitors must never review an incomplete competition job.
        coverage = _http.get("/api/v1/rankings/teams").json()["items"]
        member_team = next(x for x in coverage if x["team_id"] == job.team_id)
        assert member_team["available_frames"] == 0
        assert _http.get("/api/v1/review-queue/status").json()["pending"] == 0
        page = _http.get("/api/v1/labeling/jobs").json()["items"]
        shown = next(row for row in page if row["id"] == job_id)
        assert shown["structurally_accepted_frames"] == 1
        assert shown["structurally_pending_frames"] == 1
        assert shown["correction_round"] == 1

        correction = process_correction_once(db, sdk, db.get(SuperviselyConfig, 1))
        assert correction["status"] == "done", correction
        job = db.get(LabelingJob, job_id)
        assert job.status == "in_progress"
        assert job.correction_round == 1
        assert db.get(WorkPacket, job_id).submitted_at is None
        binding = db.scalar(select(NativeJobBinding).where(
            NativeJobBinding.job_id == job_id,
        ))
        assert binding.native_job_id != first_native_id
        assert len(list(db.scalars(select(NativeJobRevision).where(
            NativeJobRevision.job_id == job_id
        )))) == 1
        corrected_native = next(n for n in sdk.jobs if n.id == binding.native_job_id)
        assert corrected_native.images_count == 1
        assert corrected_native.image_ids == [mapped[source_ids[1]]]
        assert corrected_native.name.endswith(" (Incomplete #1)")

    sdk.labeling_job.get_annotations = full_annotations
    corrected_native.status = "on_review"
    corrected_native.finished_images_count = 1
    with maker() as db:
        sync_all(db, sdk, db.get(SuperviselyConfig, 1))
        job = db.get(LabelingJob, job_id)
        assert job.status == "on_review"
        assert db.get(WorkPacket, job_id).submitted_at is not None
        accepted = list(db.scalars(select(ReviewSample).where(
            ReviewSample.job_id == job_id
        )))
        assert len(accepted) == 2
        assert {a.media_id for a in accepted} == set(source_ids)
        assert all(a.validated_complete for a in accepted)
        assert {a.correction_round for a in accepted} == {0, 1}
        page = _http.get("/api/v1/labeling/jobs").json()["items"]
        shown = next(row for row in page if row["id"] == job_id)
        assert shown["structurally_accepted_frames"] == 2
        assert shown["structurally_pending_frames"] == 0
        assert len(list(db.scalars(select(LabelingJob).where(
            LabelingJob.id == job_id,
        )))) == 1
        status = _http.get("/api/v1/review-queue/status")
        assert status.status_code == 200
        assert status.json()["pending"] >= 2
    with maker() as db:
        sync_all(db, sdk, db.get(SuperviselyConfig, 1))
        assert len(list(db.scalars(select(ReviewSample).where(
            ReviewSample.job_id == job_id
        )))) == 2



def test_retry_old_users_info_404_correction_recovers_original_complete_frames(configured):
    """Upgrading a live attention operation should not return all 30 frames."""
    from annotatiathon.native_operations_api import queue_publish

    http, maker, csrf, _plan_id, sdk = configured
    with maker.begin() as db:
        job = db.scalar(select(LabelingJob).where(LabelingJob.sequence == 1))
        person = db.scalar(select(Person).where(Person.team_id == job.team_id))
        job.assignee_person_id = person.id
        job.release_mode = "shared_primary"
        db.get(LabelingPlan, job.plan_id).status = "running"
        db.add(WorkPacket(event_id=job.event_id, job_id=job.id,
                          person_id=person.id))
        db.flush()
        queue_publish(db, job)
        jid, original_ids = job.id, list(job.media_ids)
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        mapped = {row.media_id: row.native_image_id for row in db.scalars(
            select(NativeMediaInstance).where(NativeMediaInstance.job_id == jid)
        )}
    original_native = sdk.jobs[-1]
    original_native.status = "on_review"
    original_native.finished_images_count = 1
    original_get_annotations = sdk.labeling_job.get_annotations

    def partial_annotations(job_id, **kwargs):
        output = []
        for info, annotation in zip(
            kwargs["image_infos"], original_get_annotations(job_id, **kwargs)
        ):
            data = annotation.to_json()
            if info.id == mapped[original_ids[-1]]:
                data["objects"] = data["objects"][:1]
            output.append(Obj(to_json=lambda value=data: value))
        return output

    sdk.labeling_job.get_annotations = partial_annotations
    with maker.begin() as db:
        job = db.get(LabelingJob, jid)
        job.status = "needs_correction"
        job.review_status = "returned_incomplete"
        job.correction_round = 1
        old_op = NativeOperation(
            event_id=job.event_id, kind="restart_job", target_id=jid,
            status="attention", error_code="native_correction_uncertain",
            payload=None,
        )
        db.add(old_op)
        db.flush()
        opid = old_op.id
    retried = call(http, csrf, f"/native/operations/{opid}/retry")
    assert retried.status_code == 200, retried.text
    with maker() as db:
        corrected = process_correction_once(
            db, sdk, db.get(SuperviselyConfig, 1)
        )
        assert corrected["status"] == "done", corrected
        accepted = list(db.scalars(select(ReviewSample).where(
            ReviewSample.job_id == jid,
        )))
        assert len(accepted) == 1
        assert accepted[0].media_id == original_ids[0]
        assert accepted[0].correction_round == 0
        assert accepted[0].validated_complete
        binding = db.scalar(select(NativeJobBinding).where(
            NativeJobBinding.job_id == jid,
        ))
        followup = next(x for x in sdk.jobs if x.id == binding.native_job_id)
        assert followup.images_count == 1
        assert followup.image_ids == [mapped[original_ids[-1]]]
        assert followup.name.endswith(" (Incomplete #1)")
        assert len(list(db.scalars(select(LabelingJob).where(
            LabelingJob.id == jid,
        )))) == 1


def test_mirror_status_reports_waiting_and_running_age(configured):
    """The organiser can distinguish a stopped worker from an active mirror."""
    from annotatiathon.native_media_pool import mirror_status

    _http, maker, _csrf, _plan_id, _sdk = configured
    with maker.begin() as db:
        op = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset",
            NativeOperation.target_id == "dataset01",
        ))
        assert op is not None
        op.status = "pending"
        op.created_at = epoch() - 180
        op.attempts = 0
    with maker() as db:
        state = mirror_status(db, db.get(MediaDataset, "dataset01"))
        assert state["state"] == "pending"
        assert state["pending_seconds"] >= 180
        assert state["started_seconds_ago"] is None
        assert state["attempts"] == 0
    with maker.begin() as db:
        op = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset",
            NativeOperation.target_id == "dataset01",
        ))
        op.status = "running"
        op.attempts = 1
        op.claimed_at = epoch() - 25
    with maker() as db:
        state = mirror_status(db, db.get(MediaDataset, "dataset01"))
        assert state["state"] == "running"
        assert state["pending_seconds"] is None
        assert state["started_seconds_ago"] >= 25
        assert state["attempts"] == 1


def test_once_worker_prioritises_mirror_before_status_sync(monkeypatch):
    """Mirror must not wait for a potentially slow annotation-sync operation."""
    import annotatiathon.native_worker as worker

    class SessionStub:
        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            pass

    monkeypatch.setattr(worker, "session_factory",
                        lambda: lambda: SessionStub())
    monkeypatch.setattr(worker, "reset_pending", lambda _db: False)
    lanes = []
    monkeypatch.setattr(worker, "worker_cycle",
                        lambda lane: lanes.append(lane))
    worker.run_worker(once=True, poll_seconds=12)
    assert lanes == ["mirror", "publish", "background"]
