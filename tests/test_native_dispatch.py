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
    Person,
    StaffAccount,
    SuperviselyConfig,
    epoch,
)
from annotatiathon.native_worker import (
    process_correction_once,
    process_export_once,
    process_once,
    sync_all,
)
from annotatiathon.security import password_hash


class MockSDK:
    """Only public SDK methods required by Nightjar are emulated."""

    def __init__(self):
        self.projects = []
        self.datasets = []
        self.images = {}
        self.jobs = []
        self.nextid = 100
        self.fail_job_create_once = False
        self.project = Obj(
            get_list=self.projects_list,
            create=self.project_create,
            get_info_by_id=lambda ident: next(
                (p for p in self.projects if p.id == ident), None
            ),
        )
        self.dataset = Obj(
            get_list=self.datasets_list,
            create=self.dataset_create,
            get_info_by_id=lambda ident: next(
                (d for d in self.datasets if d.id == ident), None
            ),
        )
        self.workspace = Obj(get_info_by_id=lambda ident: Obj(id=ident, team_id=125980))
        self.image = Obj(get_list=self.images_list, upload_paths=self.upload_paths)
        self.annotation = Obj(download_json=lambda image_id: {
            "native_image_id": image_id, "labels": [{"class": "bird"}],
        })
        self.labeling_job = Obj(
            get_list=self.jobs_list,
            create=self.jobs_create,
            restart=self.jobs_restart,
            get_info_by_id=lambda ident: next((j for j in self.jobs if j.id == ident), None),
        )

    def new_id(self):
        self.nextid += 1
        return self.nextid

    def projects_list(self, workspace_id):
        return [p for p in self.projects if p.workspace_id == workspace_id]

    def project_create(self, workspace_id, name, **kwargs):
        assert kwargs["change_name_if_conflict"] is False
        p = Obj(id=self.new_id(), workspace_id=workspace_id, name=name)
        self.projects.append(p)
        return p

    def datasets_list(self, project_id):
        return [d for d in self.datasets if d.project_id == project_id]

    def dataset_create(self, project_id, name, **kwargs):
        assert kwargs["change_name_if_conflict"] is False
        item = Obj(id=self.new_id(), project_id=project_id, name=name)
        self.datasets.append(item)
        return item

    def images_list(self, dataset_id):
        return list(self.images.get(dataset_id, []))

    def upload_paths(self, dataset_id, names, paths, **kwargs):
        assert kwargs["conflict_resolution"] == "skip"
        assert len(names) == len(paths)
        for path in paths:
            assert Path(path).is_file()
        collection = self.images.setdefault(dataset_id, [])
        for name in names:
            if not any(item.name == name for item in collection):
                collection.append(Obj(id=self.new_id(), name=name))
        return [next(i for i in collection if i.name == name) for name in names]

    def jobs_list(self, team_id, dataset_id, **kwargs):
        assert team_id == 125980 and kwargs["show_disabled"] is True
        return [j for j in self.jobs if j.dataset_id == dataset_id]

    def jobs_create(self, name, dataset_id, user_ids, images_ids):
        assert len(images_ids) == len(self.images[dataset_id])
        new_jobs = []
        for uid in user_ids:
            j = Obj(
                id=self.new_id(), name=name, dataset_id=dataset_id,
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
            assigned_to_id=assignee_ids[0], status="pending",
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
        yield http, maker, login.json()["csrf_token"], plan_id, MockSDK()
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
        assert process_once(db, sdk, config) is None
        assert len(db.scalars(select(NativeJobBinding)).all()) == 2
        assert len(db.scalars(select(NativeMediaInstance)).all()) == 2
        assert len(sdk.datasets) == 1
        assert len(sdk.jobs) == 2
        assert sync_all(db, sdk, config)["changed_jobs"] == 0
        ops = list(db.scalars(select(NativeOperation)))
        assert len(ops) == 1
        sdk.jobs[0].status = "on_review"
        sdk.jobs[0].finished_images_count = 2
        sync_all(db, sdk, config)
        assert len(db.scalars(select(NativeOperation)).all()) == 1
        sdk.jobs[1].status = "on_review"
        sdk.jobs[1].finished_images_count = 1
        assert sync_all(db, sdk, config)["changed_jobs"] == 0
        assert process_once(db, sdk, config) is None
        sdk.jobs[1].finished_images_count = 2
        result = sync_all(db, sdk, config)
        assert result["changed_jobs"] == 1
        assert len(db.scalars(select(NativeOperation)).all()) == 1
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
        assert len(db.scalars(select(NativeOperation)).all()) == 2
        next_run = process_once(db, sdk, config)
        assert next_run["status"] == "done"
        assert len(sdk.datasets) == 2
        assert len(sdk.jobs) == 4
        images_by_dataset = [{i.id for i in sdk.images[d.id]} for d in sdk.datasets]
        assert images_by_dataset[0].isdisjoint(images_by_dataset[1])
    overview = http.get("/api/v1/native/overview").json()
    assert overview["native_bindings"] == 4
    assert len(overview["operations"]) == 2


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
        assert len(db.scalars(select(NativeOperation)).all()) == 3
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
        op = db.scalar(select(NativeOperation))
        opid = op.id
        assert op.status == "attention"
    retry = call(http, csrf, "/native/operations/"+opid+"/retry")
    assert retry.status_code == 200, retry.text
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        assert len(sdk.jobs) == 2
        assert len(sdk.images[sdk.datasets[0].id]) == 2
        assert len(db.scalars(select(NativeJobBinding)).all()) == 2


def test_pause_blocks_releasing_new_native_jobs(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    response = call(http, csrf, "/native/plans/"+plan_id+"/pause")
    assert response.status_code == 200
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1)) is None
        assert len(sdk.projects) == 0
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
        assert len({j.dataset_id for j in sdk.jobs}) == 2
        for native in sdk.jobs:
            native.status = "on_review"
            native.finished_images_count = 0
        assert sync_all(db, sdk, conf)["changed_jobs"] == 2
    for jobid in ids:
        r = http.get("/api/v1/review/jobs/" + jobid)
        assert r.status_code == 200
        assert r.json()["annotation_snapshot_ready"]
