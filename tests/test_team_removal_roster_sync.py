"""Teams and people must show the same membership and verified Supervisely IDs."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_monitor_assignments import mutate

from annotatiathon.app import app
from annotatiathon.db import get_db
from annotatiathon.models import (
    DEFAULT_EVENT_ID,
    Base,
    Event,
    AuditEvent,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    MediaDataset,
    MonitorPairMember,
    Person,
    StaffAccount,
    epoch,
)
from annotatiathon.security import password_hash


@pytest.fixture
def ctx(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker.begin() as db:
        db.add(Event(id=DEFAULT_EVENT_ID, name="Initial Event"))
        db.add(StaffAccount(
            email="owner@example.org", name="Owner", roles=["organiser"],
            password_hash=password_hash("StrongOrganiserPassword!"),
        ))
        db.add(StaffAccount(
            email="monitor@example.org", name="Monitor", roles=["monitor"],
            password_hash=password_hash("StrongMonitorPassword!"),
        ))

    def override():
        with maker() as db:
            yield db

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(
        "annotatiathon.auth.get_settings",
        lambda: type("Settings", (), {"app_env": "local"})(),
    )
    with TestClient(app) as client:
        logged_in = client.post("/api/v1/auth/login", json={
            "email": "owner@example.org",
            "password": "StrongOrganiserPassword!",
        })
        assert logged_in.status_code == 200
        yield client, maker, logged_in.json()["csrf_token"]
    app.dependency_overrides.clear()
    engine.dispose()


def test_participant_team_removal_keeps_people_and_records(ctx):
    http, maker, csrf = ctx
    ids = []
    for n in range(2):
        response = mutate(http, csrf, "/people", {
            "name": f"Participant {n}", "email": f"p{n}@example.org",
            "kind": "participant", "team_name": "Temporary pair",
        })
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    before = http.get("/api/v1/teams").json()["teams"]
    team = next(item for item in before if item["name"] == "Temporary pair")
    assert len(team["members"]) == 2
    roster_before = http.get("/api/v1/people").json()["items"]
    assert all(row["team_name"] == "Temporary pair" and row["team_id"] == team["id"]
               for row in roster_before if row["id"] in ids)

    removed = mutate(http, csrf, f"/teams/{team['id']}/remove")
    assert removed.status_code == 200, removed.text
    assert removed.json()["people_unassigned"] == 2
    assert removed.json()["native_mutations"] is False
    assert all(row["id"] != team["id"] for row in
               http.get("/api/v1/teams").json()["teams"])
    roster_after = http.get("/api/v1/people").json()["items"]
    assert all(row["team_name"] is None and row["team_id"] is None
               for row in roster_after if row["id"] in ids)
    assert len([row for row in roster_after if row["id"] in ids]) == 2
    assert mutate(http, csrf, f"/teams/{team['id']}/remove").json()["already_removed"]
    with maker() as db:
        assert not db.get(CompetitionTeam, team["id"]).active
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "team.remove",
            AuditEvent.target_id == team["id"],
        )) is not None


def test_monitor_pair_visible_in_people_and_can_be_removed_without_role_change(ctx):
    http, maker, csrf = ctx
    ids = []
    for n in range(2):
        response = mutate(http, csrf, "/people", {
            "name": f"Monitor {n}", "email": f"m{n}@example.org",
            "kind": "monitor",
        })
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    paired = mutate(http, csrf, "/teams/monitor-pairs", {
        "name": "Monitor trial pair", "monitor_person_ids": ids,
    })
    assert paired.status_code == 201, paired.text
    team_id = paired.json()["id"]
    with maker.begin() as db:
        native = db.get(Person, ids[0])
        native.membership_status = "joined"
        native.supervisely_user_id = 45231
        native.supervisely_login = "native-monitor"
        native.supervisely_role = "manager"
        native.native_verified_at = epoch()
    people = {row["id"]: row for row in http.get("/api/v1/people").json()["items"]}
    assert all(people[id]["team_name"] == "Monitor trial pair" for id in ids)
    assert all(people[id]["team_id"] == team_id for id in ids)
    assert people[ids[0]]["supervisely_user_id"] == 45231
    team = next(t for t in http.get("/api/v1/teams").json()["teams"]
                if t["id"] == team_id)
    assert {m["team_id"] for m in team["members"]} == {team_id}
    assert {m["id"] for m in team["members"]} == set(ids)
    removed = mutate(http, csrf, f"/teams/{team_id}/remove")
    assert removed.status_code == 200, removed.text
    assert removed.json()["monitor_pair_members_released"] == 2
    after = {row["id"]: row for row in http.get("/api/v1/people").json()["items"]}
    assert all(after[id]["team_name"] is None for id in ids)
    assert after[ids[0]]["supervisely_user_id"] == 45231
    assert after[ids[0]]["membership_status"] == "joined"
    with maker() as db:
        links = list(db.scalars(select(MonitorPairMember).where(
            MonitorPairMember.team_id == team_id,
        )))
        assert len(links) == 2 and not any(link.active for link in links)


def test_team_with_existing_jobs_cannot_be_removed(ctx):
    http, maker, csrf = ctx
    response = mutate(http, csrf, "/people", {
        "name": "Participant", "email": "job@example.org",
        "kind": "participant", "team_name": "Assigned team",
    })
    assert response.status_code == 201
    person_id = response.json()["id"]
    team_id = response.json()["team_id"]
    with maker.begin() as db:
        owner = db.scalar(select(StaffAccount.id).where(
            StaffAccount.email == "owner@example.org",
        ))
        dataset = MediaDataset(
            event_id=DEFAULT_EVENT_ID, name="Existing assignments",
            upload_kind="folder", root_path="/tmp/team-removal",
            status="ready", created_by=owner,
        )
        db.add(dataset)
        db.flush()
        plan = LabelingPlan(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            jobs_per_team=0, redundancy=1, created_by=owner,
            queue_seed="no-removal", status="running",
        )
        db.add(plan)
        db.flush()
        db.add(LabelingJob(
            event_id=DEFAULT_EVENT_ID, dataset_id=dataset.id,
            plan_id=plan.id, team_id=team_id, sequence=1,
            assignee_person_id=person_id, release_mode="manual_hold",
            media_ids=["image"], frame_count=1, video_count=1,
            morphology_counts={}, status="queued",
        ))
    rejected = mutate(http, csrf, f"/teams/{team_id}/remove")
    assert rejected.status_code == 409
    assert "labeling jobs" in rejected.json()["detail"]
    assert any(t["id"] == team_id for t in http.get("/api/v1/teams").json()["teams"])


def test_team_remove_requires_organiser_and_active_event(ctx):
    http, maker, csrf = ctx
    created = mutate(http, csrf, "/people", {
        "name": "Participant", "email": "guard@example.org",
        "kind": "participant", "team_name": "Protected team",
    })
    team_id = created.json()["team_id"]
    login = http.post("/api/v1/auth/login", json={
        "email": "monitor@example.org",
        "password": "StrongMonitorPassword!",
    })
    assert login.status_code == 200
    monitor_token = login.json()["csrf_token"]
    assert mutate(http, monitor_token, f"/teams/{team_id}/remove").status_code == 403
    assert mutate(http, csrf, "/events/"+DEFAULT_EVENT_ID+"/archive", {
        "confirmation": "Initial Event",
    }).status_code == 200
    assert mutate(http, csrf, f"/teams/{team_id}/remove").status_code == 409
    with maker() as db:
        assert db.get(CompetitionTeam, team_id).active
