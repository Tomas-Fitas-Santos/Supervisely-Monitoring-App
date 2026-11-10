"""Dataset coverage counts rely on real media rows and observed interval labels.

These isolated fixtures exercise the real FastAPI + ORM contracts. No demo
mode or fabricated Supervisely success is introduced into the application.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    Event,
    MediaAsset,
    MediaDataset,
    StaffAccount,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Real test event"))
        owner = StaffAccount(
            email="owner@example.org", name="Owner",
            roles=["organiser"], password_hash=password_hash("StrongLocalPassword!"),
        )
        monitor = StaffAccount(
            email="monitor@example.org", name="Monitor",
            roles=["monitor"], password_hash=password_hash("StrongMonitorPassword!"),
        )
        db.add_all([owner, monitor])
        db.flush()
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Bird clips", upload_kind="folder",
            status="ready", root_path="/never-accessed-media",
            frame_count=6, image_count=2, video_count=3, created_by=owner.id,
        )
        db.add(dataset)
        db.flush()
        did = dataset.id
        for source, group, paths in (
            ("PasserDomesticus", "HouseSparrowWalking", ["0001.jpg", "0002.jpg", "0003.jpg"]),
            ("MysteryBird", "UnknownVideo", ["0001.jpg", "0002.jpg"]),
            ("SelasphorusRufus", "Hovering", ["0001.jpg"]),
        ):
            for p in paths:
                rel = "videos/" + source + "/" + group + "/" + p
                db.add(MediaAsset(
                    event_id=DEFAULT_EVENT_ID, dataset_id=did,
                    relative_path=rel, morphology=source, kind="video_frame",
                    video_group=source + "/" + group,
                    size_bytes=111, sha256="a"*64,
                ))
        for p in ("01.jpg", "02.jpg"):
            db.add(MediaAsset(
                event_id=DEFAULT_EVENT_ID, dataset_id=did,
                relative_path="images/PasserDomesticus/"+p,
                morphology="PasserDomesticus", kind="image",
                size_bytes=111, sha256="b"*64,
            ))
    def override_db():
        with maker() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as http:
        owner = http.post("/api/v1/auth/login",json={
            "email":"owner@example.org","password":"StrongLocalPassword!",
        })
        assert owner.status_code == 200
        owner_token = owner.json()["csrf_token"]
        yield http, maker, did, owner_token
    app.dependency_overrides.clear()
    engine.dispose()


def method(http, csrf, name, path, payload=None):
    return http.request(
        name, "/api/v1"+path, json=payload,
        headers={"X-CSRF-Token": csrf},
    )


def test_ecomorphology_counts_are_unique_frames_and_include_unknown(ctx):
    http, _, did, _ = ctx
    r = http.get("/api/v1/coverage/datasets/"+did)
    assert r.status_code == 200, r.text
    content = r.json()
    assert content["totals"] == {
        "frames":6,"images":2,"annotatable_media":8,"videos":3,
        "species_folders":3,"unclassified_frames":2,"unclassified_images":0,
        "tagged_frames":0,"unlabeled_frames":6,
    }
    groups = {g["name"]:g for g in content["groups"]}
    assert len(groups) == 17  # 16 workbook groups plus explicit Unclassified
    assert groups["General perching birds"]["frames"] == 3
    assert groups["General perching birds"]["images"] == 2
    assert groups["Hummingbird-type birds"]["frames"] == 1
    assert groups["Unclassified"]["frames"] == 2
    assert sum(m["frames"] for m in content["movements"]) == 6
    folders = {r["source_folder"]:r for r in content["folders"]}
    assert folders["PasserDomesticus"]["species_name"] == "House Sparrow"
    assert folders["PasserDomesticus"]["ecomorphology"] == "General perching birds"
    assert folders["MysteryBird"]["ecomorphology"] == "Unclassified"


def test_review_video_frames_pagination_is_lexically_ordered(ctx):
    http, _, did, _ = ctx
    r = http.get("/api/v1/coverage/datasets/"+did+"/video-frames",params={
        "video_group":"PasserDomesticus/HouseSparrowWalking","size":2,"page":2,
    })
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3
    assert [x["frame"] for x in r.json()["items"]] == [3]
    assert r.json()["items"][0]["relative_path"].endswith("0003.jpg")


def test_observed_motion_ranges_are_disjoint_and_not_inferred(ctx):
    http, _, did, csrf = ctx
    base="/coverage/datasets/"+did
    bad = method(http,csrf,"POST",base+"/movements",{
        "video_group":"PasserDomesticus/HouseSparrowWalking",
        "start_frame":1,"end_frame":4,"movement_type":"head_bobbing",
    })
    assert bad.status_code == 422
    success = method(http,csrf,"POST",base+"/movements",{
        "video_group":"PasserDomesticus/HouseSparrowWalking",
        "start_frame":1,"end_frame":2,"movement_type":"head_bobbing",
        "notes":"Visible head translation while walking",
    })
    assert success.status_code == 201, success.text
    segment_id=success.json()["id"]
    overlap = method(http,csrf,"POST",base+"/movements",{
        "video_group":"PasserDomesticus/HouseSparrowWalking",
        "start_frame":2,"end_frame":3,"movement_type":"rapid_head_turn",
    })
    assert overlap.status_code == 409
    after = http.get("/api/v1"+base).json()
    assert after["totals"]["tagged_frames"] == 2
    assert after["totals"]["unlabeled_frames"] == 4
    assert sum(x["frames"] for x in after["movements"]) == 6
    assert next(x for x in after["movements"] if x["type"]=="head_bobbing")["frames"] == 2
    csv = http.get("/api/v1"+base+"/export.csv")
    assert csv.status_code == 200
    assert "head_bobbing,1,2,2" in csv.text
    removed = method(http,csrf,"DELETE",base+"/movements/"+segment_id)
    assert removed.status_code == 200
    assert http.get("/api/v1"+base).json()["totals"]["tagged_frames"] == 0


def test_manual_folder_assignment_updates_ecomorphology_without_changing_assets(ctx):
    http, maker, did, csrf = ctx
    base="/coverage/datasets/"+did
    result=method(http,csrf,"PUT",base+"/classification",{
        "source_folder":"MysteryBird",
        "ecomorphology":"Owls","species_name":"Verified unknown owl species",
    })
    assert result.status_code == 200, result.text
    updated = http.get("/api/v1"+base).json()
    groups = {g["name"]:g for g in updated["groups"]}
    assert groups["Owls"]["frames"] == 2
    assert groups["Unclassified"]["frames"] == 0
    row = next(f for f in updated["folders"] if f["source_folder"]=="MysteryBird")
    assert row["manual"] is True
    assert row["species_name"] == "Verified unknown owl species"
    with maker() as db:
        from sqlalchemy import func,select
        assert db.scalar(select(func.count(MediaAsset.id)).where(
            MediaAsset.dataset_id == did
        )) == 8
    wrong=method(http,csrf,"PUT",base+"/classification",{
        "source_folder":"MysteryBird","ecomorphology":"Nonexistent Group",
    })
    assert wrong.status_code == 422


def test_monitor_may_inspect_but_cannot_edit(ctx):
    http, _, did, owner_csrf = ctx
    http.post("/api/v1/auth/logout", headers={"X-CSRF-Token": owner_csrf})
    r=http.post("/api/v1/auth/login",json={
        "email":"monitor@example.org","password":"StrongMonitorPassword!",
    })
    assert r.status_code == 200
    csrf=r.json()["csrf_token"]
    assert http.get("/api/v1/coverage/datasets/"+did).status_code == 200
    forbidden=method(http,csrf,"POST","/coverage/datasets/"+did+"/movements",{
        "video_group":"PasserDomesticus/HouseSparrowWalking",
        "start_frame":1,"end_frame":1,"movement_type":"pecking",
    })
    assert forbidden.status_code == 403
    forbidden=method(http,csrf,"PUT","/coverage/datasets/"+did+"/classification",{
        "source_folder":"MysteryBird","ecomorphology":"Owls",
    })
    assert forbidden.status_code == 403
