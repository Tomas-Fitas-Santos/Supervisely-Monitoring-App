"""Regression contracts for non-destructive append and opt-in balanced claims.

No synthetic load benchmark: these are narrow source identity/transaction
checks and reuse the disposable local dataset test fixture.
"""
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_dataset_workflows import act, teams

from annotatiathon.app import app
from annotatiathon.db import get_db

from annotatiathon.balanced_queue import process_preparation_once
from annotatiathon.dataset_append import process_append_once
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    BalancedVideoRun,
    CompetitionTeam,
    DatasetAppendBatch,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaDataset,
    MediaVideo,
    NativeOperation,
    Person,
    StaffAccount,
)
from annotatiathon.security import password_hash
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def dataset_ctx(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner",
            roles=["organiser"], password_hash=password_hash("StrongLocalPassword!"),
        ))
    def override():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    monkeypatch.setattr(
        "annotatiathon.datasets_api.get_settings",
        lambda: type("Settings", (), {
            "local_data_dir": str(tmp_path),
            "max_dataset_bytes": 150_000_000_000,
            "max_dataset_assets": 250000,
        })(),
    )
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={
            "email": "owner@example.org", "password": "StrongLocalPassword!",
        })
        assert login.status_code == 200
        yield client, maker, login.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def _create_two_video_dataset(maker, tmp_path):
    with maker.begin() as db:
        owner = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset_root = tmp_path / "balanced"
        (dataset_root / "media").mkdir(parents=True)
        ds = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Balanced imported clips",
            root_path=str(dataset_root), upload_kind="folder",
            extraction_policy="all_frames", status="ready",
            created_by=owner, frame_count=30, video_count=2,
        )
        db.add(ds)
        db.flush()
        for folder, title, start, frames in (
            ("ColumbaPalumbus", "clipA", 41, 16),
            ("AnasUndulata", "clipB", 0, 14),
        ):
            group = f"{folder}/{title}"
            video = MediaVideo(
                event_id=DEFAULT_EVENT_ID, dataset_id=ds.id,
                video_group=group, extraction_policy="all_frames",
                density="full", identity_status="complete",
                frame_count=frames, source_frame_count=frames,
            )
            db.add(video)
            db.flush()
            for ordinal in range(start, start + frames):
                db.add(MediaAsset(
                    event_id=DEFAULT_EVENT_ID, dataset_id=ds.id,
                    video_id=video.id, video_group=group,
                    relative_path=f"videos/{folder}/{title}/frame_{ordinal:06d}.jpg",
                    morphology=folder, frame_index=ordinal,
                    kind="video_frame", size_bytes=1, sha256="0" * 64,
                ))
        return ds.id


def test_balanced_preparation_without_roster_and_16_plus_14_job(dataset_ctx, tmp_path):
    client, maker, csrf = dataset_ctx
    dsid = _create_two_video_dataset(maker, tmp_path)

    # Explicit preparation is not automatically performed by import or mirror.
    state = client.get(f"/api/v1/datasets/{dsid}/balanced-preparation")
    assert state.status_code == 200
    assert state.json()["status"] == "not_prepared"
    requested = act(client, csrf, f"/datasets/{dsid}/balanced-preparation")
    assert requested.status_code == 202, requested.text
    with maker() as db:
        result = process_preparation_once(db)
    assert result["status"] == "done"
    preview = client.get(f"/api/v1/datasets/{dsid}/balanced-preparation").json()
    assert preview["status"] == "prepared"
    assert preview["summary"]["eligible_frames"] == 30
    assert preview["summary"]["eligible_videos"] == 2

    draft = act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "mode": "balanced_30_v1",
    })
    assert draft.status_code == 201, draft.text
    plan_id = draft.json()["plan_id"]
    assert act(client, csrf, f"/labeling/plans/{plan_id}/approve").status_code == 200

    teams(client, csrf)
    with maker.begin() as db:
        plan = db.get(LabelingPlan, plan_id)
        plan.status = "running"  # Publication/mirroring is deliberately out of scope.
        team = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.event_id == DEFAULT_EVENT_ID,
            CompetitionTeam.name == "Team 0",
        ))
        person = db.scalar(select(Person).where(
            Person.event_id == DEFAULT_EVENT_ID,
            Person.team_id == team.id,
        ))
        person.check_in_at = 1
        person.supervisely_user_id = 1001
        person.supervisely_role = "annotator"
        person.native_verified_at = 1
        person.membership_status = "joined"
        team_id, person_id = team.id, person.id

    with maker.begin() as db:
        job = claim_primary_block(db, plan_id, team_id, person_id)
        assert job is not None
        assert job.frame_count == 30
        assert job.video_count == 2
        job_id = job.id
    with maker() as db:
        job = db.get(LabelingJob, job_id)
        assets = {row.id: (row.video_group, row.frame_index)
                  for row in db.scalars(select(MediaAsset).where(
                      MediaAsset.dataset_id == dsid))}
        actual = [assets[mid] for mid in job.media_ids]
        assert actual[:16] == [
            ("ColumbaPalumbus/clipA", i) for i in range(41, 57)
        ]
        assert actual[16:] == [
            ("AnasUndulata/clipB", i) for i in range(14)
        ]
        assert sorted(row.next_offset for row in db.scalars(select(
            BalancedVideoRun).where(BalancedVideoRun.plan_id == plan_id))) == [14, 16]
        assert db.scalar(select(NativeOperation.id).where(
            NativeOperation.kind == "publish_job",
            NativeOperation.target_id == job_id,
        )) is not None


def test_append_batch_preserves_existing_video_and_blocks_early_preparation(
    dataset_ctx, tmp_path, monkeypatch,
):
    client, maker, csrf = dataset_ctx
    dsid = _create_two_video_dataset(maker, tmp_path)
    monkeypatch.setattr(
        "annotatiathon.dataset_append.get_settings",
        lambda: SimpleNamespace(max_dataset_bytes=10**9, max_dataset_assets=1000),
    )
    created = act(client, csrf, f"/datasets/{dsid}/append-batches", {
        "upload_kind": "folder",
    })
    assert created.status_code == 201, created.text
    bid = created.json()["id"]
    blocked = act(client, csrf, f"/datasets/{dsid}/balanced-preparation")
    assert blocked.status_code == 409

    buffer = io.BytesIO()
    Image.new("RGB", (12, 12)).save(buffer, format="JPEG")
    raw = buffer.getvalue()
    rel = "ColumbaPalumbus/clipC/frame_000125.jpg"
    uploaded = client.put(
        f"/api/v1/datasets/{dsid}/append-batches/{bid}/chunk",
        params={"relative_path": rel, "offset": 0, "total": len(raw)},
        data=raw, headers={"X-CSRF-Token": csrf},
    )
    assert uploaded.status_code == 200, uploaded.text
    finished = act(client, csrf, f"/datasets/{dsid}/append-batches/{bid}/finish")
    assert finished.status_code == 202, finished.text
    with maker() as db:
        result = process_append_once(db)
    assert result["status"] == "done", result
    with maker() as db:
        ds = db.get(MediaDataset, dsid)
        assert ds.inventory_revision == 2
        assert ds.frame_count == 31
        assert ds.video_count == 3
        assert db.get(DatasetAppendBatch, bid).status == "committed"
        names = set(db.scalars(select(MediaVideo.video_group).where(
            MediaVideo.dataset_id == dsid)))
        assert names == {
            "ColumbaPalumbus/clipA", "AnasUndulata/clipB",
            "ColumbaPalumbus/clipC",
        }
        operation = db.scalar(select(NativeOperation).where(
            NativeOperation.kind == "mirror_dataset",
            NativeOperation.target_id == dsid,
        ))
        assert operation.status == "pending"
        assert operation.payload["inventory_revision"] == 2

    requested = act(client, csrf, f"/datasets/{dsid}/balanced-preparation")
    assert requested.status_code == 202, requested.text
    with maker() as db:
        assert process_preparation_once(db)["status"] == "done"
    preview = client.get(f"/api/v1/datasets/{dsid}/balanced-preparation").json()
    assert preview["summary"]["eligible_frames"] == 31
