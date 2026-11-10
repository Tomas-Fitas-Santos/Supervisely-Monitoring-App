"""Local end-to-end acceptance: ingest, plan, review, closeout without demo mode."""
import io
import zipfile

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
    Event,
    LabelingJob,
    MediaAsset,
    StaffAccount,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch, tmp_path):
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


def act(client, csrf, path, body=None, method="POST"):
    return client.request(
        method, "/api/v1" + path, json=body, headers={"X-CSRF-Token": csrf},
    )


def ingest(client, csrf):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in (
            "Dataset/images/wings/a.jpg",
            "Dataset/images/beaks/b.jpg",
            "Dataset/videos/wings/Flight01/0001.jpg",
            "Dataset/videos/wings/Flight01/0002.jpg",
        ):
            zf.writestr(filename, b"small bird image bytes")
    source = output.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Local Media", "upload_kind": "zip",
    })
    assert created.status_code == 201, created.text
    dsid = created.json()["id"]
    midpoint = len(source)//2
    for offset, part in ((0, source[:midpoint]), (midpoint, source[midpoint:])):
        result = client.put(
            "/api/v1/datasets/uploads/"+dsid+"/chunk",
            params={"relative_path": "source.zip", "offset": offset, "total": len(source)},
            data=part, headers={"X-CSRF-Token": csrf},
        )
        assert result.status_code == 200, result.text
    done = act(client, csrf, "/datasets/"+dsid+"/finalize")
    assert done.status_code == 200, done.text
    assert done.json()["image_count"] == 2
    assert done.json()["frame_count"] == 2
    assert done.json()["video_count"] == 1
    assert act(client, csrf, "/datasets/"+dsid+"/finalize").status_code == 200
    summary = client.get("/api/v1/datasets/"+dsid+"/summary").json()
    assert summary["total_assets"] == 4
    assert len(summary["morphologies"]) == 3
    return dsid


def teams(client, csrf):
    for n in range(3):
        for i in range(2):
            result = act(client, csrf, "/people", {
                "name": f"Participant {n}-{i}",
                "email": f"p{n}-{i}@example.org",
                "kind": "participant",
                "team_name": f"Team {n}",
            })
            assert result.status_code == 201, result.text


def test_local_upload_planning_and_atomic_video_groups(ctx):
    client, maker, csrf = ctx
    dsid = ingest(client, csrf)
    teams(client, csrf)
    plan = act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "jobs_per_team": 2, "redundancy": 2,
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["assets"] == 4
    assert plan.json()["assignment_copies"] == 8
    approve = act(client, csrf, "/labeling/plans/"+plan.json()["plan_id"]+"/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    jobs = client.get("/api/v1/labeling/jobs").json()["items"]
    assert jobs
    with maker() as db:
        assigned: dict[str, set[str]] = {}
        for job in db.scalars(select(LabelingJob)):
            for mid in job.media_ids:
                assigned.setdefault(mid, set()).add(job.team_id)
        assert all(len(tids) == 2 for tids in assigned.values())
        assets = list(db.scalars(select(MediaAsset)))
        frames = {a.id for a in assets if a.kind == "video_frame"}
        for team in {job.team_id for job in db.scalars(select(LabelingJob))}:
            groups = [
                {m for m in job.media_ids if m in frames}
                for job in db.scalars(select(LabelingJob).where(
                    LabelingJob.team_id == team
                ))
            ]
            assert all(not item or item == frames for item in groups)
    assert client.get("/api/v1/datasets/"+dsid+"/assets?page=1&size=2").json()["total"] == 4
    image_id = client.get("/api/v1/datasets/"+dsid+"/assets").json()["items"][0]["id"]
    preview = client.get("/api/v1/review/datasets/"+dsid+"/assets/"+image_id+"/image")
    assert preview.status_code == 200
    assert preview.content == b"small bird image bytes"


def test_review_and_csv_and_closeout_require_accepted_jobs(ctx):
    client, maker, csrf = ctx
    dsid = ingest(client, csrf)
    teams(client, csrf)
    result = act(client, csrf, "/labeling/plans", {
        "dataset_id": dsid, "jobs_per_team": 2, "redundancy": 1,
    })
    assert result.status_code == 201, result.text
    plan_id = result.json()["plan_id"]
    assert act(client, csrf, "/labeling/plans/"+plan_id+"/approve").status_code == 200
    with maker.begin() as db:
        first = db.scalar(select(LabelingJob).order_by(LabelingJob.id))
        first.status = "on_review"
        job_id = first.id
        media_id = first.media_ids[0]
    frame = act(client, csrf, "/review/jobs/"+job_id+"/frames", {
        "media_id": media_id, "verdict": "good",
        "correct_keypoints": 8, "missing_keypoints": 2, "incorrect_keypoints": 1,
        "visible_keypoints": 7, "occluded_keypoints": 2,
        "temporal_consistency": True,
    }, "PUT")
    assert frame.status_code == 200, frame.text
    assert frame.json()["keypoint_score"] == 6
    reviewed = client.get("/api/v1/review/jobs/"+job_id)
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_frames"] == 1
    assert act(client, csrf, "/review/jobs/"+job_id+"/decision", {
        "decision": "accept",
    }).status_code == 200
    report = client.get("/api/v1/closeout/report").json()
    assert report["reviews"]["keypoint_score"] == 6
    assert report["labeling"]["jobs_needing_action"] > 0
    assert act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    }).status_code == 409
    with maker.begin() as db:
        for job in db.scalars(select(LabelingJob).where(
            LabelingJob.status == "queued"
        )):
            job.status = "completed"
            job.review_status = "accepted"
    closed = act(client, csrf, "/closeout/finalize", {
        "confirmation": "CLOSE Initial Event",
    })
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "archived"
    assert client.get("/api/v1/closeout/report").json()["event_status"] == "archived"
    csv_response = client.get("/api/v1/closeout/exports/media")
    assert csv_response.status_code == 200
    assert csv_response.text.count("wings") >= 2
    assert act(client, csrf, "/datasets/uploads", {
        "name": "Blocked", "upload_kind": "zip",
    }).status_code == 409


def test_dataset_upload_rejects_unsafe_path(ctx):
    client, _maker, csrf = ctx
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Safe Test", "upload_kind": "folder",
    })
    assert created.status_code == 201
    dsid = created.json()["id"]
    invalid = client.put("/api/v1/datasets/uploads/"+dsid+"/chunk",
        params={"relative_path": "../outside.jpg", "offset": 0, "total": 1},
        data=b"x", headers={"X-CSRF-Token": csrf})
    assert invalid.status_code == 422



def test_monitor_pair_creation_disband_and_plan_provenance(ctx):
    client, maker, csrf = ctx
    for index in range(3):
        response = act(client, csrf, "/people", {
            "name": f"Monitor {index}",
            "email": f"monitor{index}@example.org",
            "kind": "monitor",
        })
        assert response.status_code == 201, response.text
    roster = client.get("/api/v1/people?kind=monitor&size=10").json()["items"]
    selected = [p["id"] for p in roster[:2]]
    made = act(client, csrf, "/teams/monitor-pairs", {
        "name": "Monitor Pair One", "monitor_person_ids": selected,
    })
    assert made.status_code == 201, made.text
    tid = made.json()["id"]
    teams = client.get("/api/v1/teams").json()["teams"]
    team = next(t for t in teams if t["id"] == tid)
    assert team["member_source"] == "monitor_pair"
    assert len(team["members"]) == 2
    assert all(p["kind"] == "monitor" for p in team["members"])
    duplicate = act(client, csrf, "/teams/monitor-pairs", {
        "name": "Monitor Pair Two", "monitor_person_ids": selected,
    })
    assert duplicate.status_code == 409
    disband = act(client, csrf, "/teams/monitor-pairs/"+tid+"/disband")
    assert disband.status_code == 200
    assert disband.json()["status"] == "disbanded"
    assert all(t["id"] != tid for t in client.get("/api/v1/teams").json()["teams"])
    reused = act(client, csrf, "/teams/monitor-pairs", {
        "name": "Monitor Pair Two", "monitor_person_ids": selected,
    })
    assert reused.status_code == 201, reused.text
    dataset_id = ingest(client, csrf)
    planned = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "jobs_per_team": 2, "redundancy": 1,
    })
    assert planned.status_code == 201, planned.text
    blocked = act(
        client, csrf,
        "/teams/monitor-pairs/"+reused.json()["id"]+"/disband",
    )
    assert blocked.status_code == 409
    with maker() as db:
        from annotatiathon.models import MonitorPairMember
        active = list(db.scalars(select(MonitorPairMember).where(
            MonitorPairMember.active.is_(True)
        )))
        assert len(active) == 2
    with maker.begin() as db:
        for job in db.scalars(select(LabelingJob).where(
            LabelingJob.team_id == reused.json()["id"]
        )):
            job.status = "completed"
            job.review_status = "accepted"
    finished = act(
        client, csrf, "/teams/monitor-pairs/"+reused.json()["id"]+"/disband",
    )
    assert finished.status_code == 200, finished.text
    with maker() as db:
        assert len(db.scalars(select(LabelingJob)).all()) >= 1
        assert len(db.scalars(select(MonitorPairMember).where(
            MonitorPairMember.active.is_(True)
        )).all()) == 0



def test_interrupt_resume_and_delete_broken_upload(ctx):
    client, _maker, csrf = ctx
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Interrupted ZIP", "upload_kind": "zip",
    })
    assert created.status_code == 201
    dsid = created.json()["id"]
    part = b"hello interrupted zip"
    chunk = client.put(
        f"/api/v1/datasets/uploads/{dsid}/chunk",
        params={"relative_path": "source.zip", "offset": 0, "total": 100},
        data=part, headers={"X-CSRF-Token": csrf},
    )
    assert chunk.status_code == 200, chunk.text
    active_delete = act(client, csrf, f"/datasets/{dsid}", method="DELETE")
    assert active_delete.status_code == 409
    stopped = act(client, csrf, f"/datasets/{dsid}/interrupt")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "interrupted"
    stored = client.get(f"/api/v1/datasets/{dsid}/upload-status",
                        params={"include_files": "true"})
    assert stored.status_code == 200
    assert stored.json()["file_progress"] == [{
        "relative_path": "source.zip", "received_bytes": len(part),
        "expected_bytes": 100,
    }]
    resumed = act(client, csrf, f"/datasets/{dsid}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "uploading"
    assert act(client, csrf, f"/datasets/{dsid}/interrupt").status_code == 200
    removed = act(client, csrf, f"/datasets/{dsid}", method="DELETE")
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert client.get(f"/api/v1/datasets/{dsid}/upload-status").status_code == 404
    assert not any(row["id"] == dsid for row in client.get("/api/v1/datasets").json()["items"])


def test_stale_upload_can_be_deleted_without_page_state(ctx):
    client, maker, csrf = ctx
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Abandoned old upload", "upload_kind": "zip",
    })
    dsid = created.json()["id"]
    from annotatiathon.models import MediaDataset, epoch
    with maker.begin() as db:
        db.get(MediaDataset, dsid).last_activity_at = epoch() - 3600
    assert client.get("/api/v1/datasets").json()["items"][0]["status"] == "interrupted"
    assert act(client, csrf, f"/datasets/{dsid}", method="DELETE").status_code == 200


def test_ready_dataset_is_protected_from_cleanup(ctx):
    client, _maker, csrf = ctx
    dsid = ingest(client, csrf)
    assert act(client, csrf, f"/datasets/{dsid}", method="DELETE").status_code == 409
    assert client.get(f"/api/v1/datasets/{dsid}/summary").status_code == 200


def upload_archive(client, csrf, members: dict[str, bytes]) -> dict:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in members.items():
            archive.writestr(path, data)
    payload = stream.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Bird Species Videos", "upload_kind": "zip",
    })
    assert created.status_code == 201, created.text
    dsid = created.json()["id"]
    transferred = client.put(
        f"/api/v1/datasets/uploads/{dsid}/chunk",
        params={"relative_path": "source.zip", "offset": 0, "total": len(payload)},
        data=payload, headers={"X-CSRF-Token": csrf},
    )
    assert transferred.status_code == 200, transferred.text
    return {"id": dsid, "created": created.json()}


def test_species_first_frame_folders_without_images_videos_prefix(ctx):
    client, maker, csrf = ctx
    dataset = upload_archive(client, csrf, {
        "BirdGaze/CorvusCorone/flight_01/000001.jpg": b"bird one",
        "BirdGaze/CorvusCorone/flight_01/000002.jpg": b"bird two",
        "BirdGaze/ColumbaPalumbus/feeding_02/000001.jpg": b"bird three",
    })
    dsid = dataset["id"]
    result = act(client, csrf, f"/datasets/{dsid}/finalize")
    assert result.status_code == 200, result.text
    assert result.json()["image_count"] == 0
    assert result.json()["frame_count"] == 3
    assert result.json()["video_count"] == 2
    with maker() as db:
        assets = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dsid
        )))
    assert all(item.kind == "video_frame" for item in assets)
    assert {item.video_group for item in assets} == {
        "CorvusCorone/flight_01", "ColumbaPalumbus/feeding_02"
    }
    assert all(item.relative_path.startswith("videos/") for item in assets)


def test_single_species_without_dataset_wrapper_is_not_stripped(ctx):
    client, maker, csrf = ctx
    dataset = upload_archive(client, csrf, {
        "CorvusCorone/flight_01/000001.jpg": b"bird one",
        "CorvusCorone/flight_01/000002.jpg": b"bird two",
    })
    result = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert result.status_code == 200, result.text
    assert result.json()["video_count"] == 1
    with maker() as db:
        assert {item.morphology for item in db.scalars(select(MediaAsset))} == {
            "CorvusCorone"
        }


def test_species_first_raw_video_files_become_one_video_per_job(ctx, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    def sample_frames(command, **_kwargs):
        # Simulate FFmpeg writing deterministic JPEG files while retaining
        # source-video bytes separately. Real FFmpeg is installed in the API image.
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        for frame in (1, 2):
            (target.parent / f"frame_{frame:08d}.jpg").write_bytes(b"jpg" + bytes([frame]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("annotatiathon.datasets_api.subprocess.run", sample_frames)
    client, maker, csrf = ctx
    dataset = upload_archive(client, csrf, {
        "BirdGaze/CorvusCorone/flight_01.mp4": b"sample video one",
        "BirdGaze/ColumbaPalumbus/feeding_02.mkv": b"sample video two",
    })
    result = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert result.status_code == 200, result.text
    assert result.json()["image_count"] == 0
    assert result.json()["frame_count"] == 4
    assert result.json()["video_count"] == 2
    with maker() as db:
        assets = list(db.scalars(select(MediaAsset)))
        assert {item.video_group for item in assets} == {
            "CorvusCorone/flight_01", "ColumbaPalumbus/feeding_02"
        }
        assert all(item.kind == "video_frame" for item in assets)
    teams(client, csrf)
    plan = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset["id"], "jobs_per_team": 2, "redundancy": 1,
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["job_count"] == 2
    assert act(client, csrf,
               "/labeling/plans/" + plan.json()["plan_id"] + "/approve").status_code == 200
    with maker() as db:
        jobs = list(db.scalars(select(LabelingJob)))
        assert all(job.video_count == 1 for job in jobs)
        assert all(job.frame_count == 2 for job in jobs)


def test_species_first_video_rejects_mixed_independent_images(ctx):
    client, _maker, csrf = ctx
    dataset = upload_archive(client, csrf, {
        "BirdGaze/CorvusCorone/flight_01/000001.jpg": b"good frame",
        "BirdGaze/CorvusCorone/photo.jpg": b"not a video frame folder",
    })
    rejected = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert rejected.status_code == 422, rejected.text
