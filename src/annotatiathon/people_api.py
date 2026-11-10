"""Roster and team management, including verified joined-member role synchronization."""
import csv
import hashlib
import io
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, normalized_email, requires
from .db import get_db
from .events_api import current_event
from .supervisely_client import NativeAPIError, change_member_role
from .models import (
    AuditEvent,
    CompetitionTeam,
    ImportBatch,
    MonitorAssignment,
    MonitorPairMember,
    Person,
    StaffAccount,
    StaffSession,
    SuperviselyConfig,
)

router = APIRouter(prefix="/api/v1", tags=["people"])
VIEWER = ("organiser", "monitor", "reception", "activity", "distribution", "observer")


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    kind: Literal["participant", "monitor"] = "participant"
    team_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def email_key(cls, value: str) -> str:
        return normalized_email(value)

    @field_validator("name", "team_name")
    @classmethod
    def trim(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    kind: Literal["participant", "monitor"] | None = None
    team_name: str | None = Field(default=None, max_length=200)
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def email_key(cls, value: str | None) -> str | None:
        return normalized_email(value) if value else value


class CSVMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str
    kind: str | None = None
    team: str | None = None


class RosterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv_text: str = Field(min_length=1, max_length=5_000_000)
    mapping: CSVMapping
    default_kind: Literal["participant", "monitor"] = "participant"


def team_id(db: Session, team_name: str | None, event_id: str) -> str | None:
    name = (team_name or "").strip()
    if not name:
        return None
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.name == name, CompetitionTeam.event_id == event_id
    ))
    if team is None:
        team = CompetitionTeam(name=name, event_id=event_id)
        db.add(team)
        db.flush()
    elif not team.active:
        team.active = True
    return team.id


def person_record(p: Person, team: str | None = None,
                  table_number: int | None = None) -> dict:
    return {
        "id": p.id, "name": p.name, "email": p.email, "kind": p.kind,
        "team_id": p.team_id, "team_name": team, "active": p.active,
        "membership_status": p.membership_status,
        "monitor_registration_approved": bool(
            p.kind == "monitor" and p.active and (
                (p.source_fields or {}).get(
                    "monitor_registration_approved",
                    (p.source_fields or {}).get("source") != "supervisely_member_sync",
                ) is True
            )
        ),
        "native_auto_imported": (p.source_fields or {}).get("source") == "supervisely_member_sync",
        "supervisely_login": p.supervisely_login,
        "supervisely_role": p.supervisely_role,
        "native_verified_at": p.native_verified_at,
        "check_in_at": p.check_in_at,
        "table_number": table_number if p.team_id else p.table_number,
    }


def audit(db: Session, actor: str, event_id: str, action: str, target: str | None,
          details: dict) -> None:
    db.add(AuditEvent(staff_id=actor, event_id=event_id, action=action, target_type="person",
                      target_id=target, details=details))


@router.get("/people")
def list_people(
    search: str = Query(default="", max_length=200),
    kind: Literal["participant", "monitor", "all"] = "all",
    membership: str = Query(default="all", max_length=30),
    attendance: Literal["all", "present", "missing"] = "all",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _ctx: AuthContext = Depends(requires(*VIEWER)),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    query = select(Person).where(Person.active.is_(True), Person.event_id == event.id)
    if kind != "all":
        query = query.where(Person.kind == kind)
    if membership != "all":
        query = query.where(Person.membership_status == membership)
    if attendance == "present":
        query = query.where(Person.check_in_at.is_not(None))
    elif attendance == "missing":
        query = query.where(Person.check_in_at.is_(None))
    if search.strip():
        key = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(Person.name).like(key) | func.lower(Person.email).like(key)
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    people = db.scalars(
        query.order_by(Person.kind.desc(), Person.name, Person.email)
        .offset((page - 1) * size).limit(size)
    ).all()
    teams_by_id = {t.id: t for t in db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id
    ))}
    return {
        "items": [person_record(
            p, teams_by_id[p.team_id].name if p.team_id in teams_by_id else None,
            teams_by_id[p.team_id].table_number if p.team_id in teams_by_id else None,
        ) for p in people],
        "total": total, "page": page, "size": size,
    }


@router.get("/teams")
def list_teams(
    sort: Literal["name", "check_in", "size"] = "name",
    _ctx: AuthContext = Depends(requires(*VIEWER)),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id, CompetitionTeam.active.is_(True)
    )))
    people = list(db.scalars(select(Person).where(
        Person.event_id == event.id, Person.active.is_(True)
    )))
    monitor_names = {p.id: p.name for p in people if p.kind == "monitor"}
    responsibility = {
        item.team_id: item.monitor_person_id for item in db.scalars(
            select(MonitorAssignment).where(
                MonitorAssignment.event_id == event.id,
                MonitorAssignment.active.is_(True),
            )
        )
    }
    grouped: dict[str | None, list[Person]] = defaultdict(list)
    for person in people:
        grouped[person.team_id if person.kind == "participant" else None].append(person)
    people_by_id = {p.id: p for p in people}
    monitor_pairs: dict[str, list[Person]] = defaultdict(list)
    for link in db.scalars(select(MonitorPairMember).where(
        MonitorPairMember.event_id == event.id, MonitorPairMember.active.is_(True),
    )):
        person = people_by_id.get(link.person_id)
        if person and person.kind == "monitor":
            monitor_pairs[link.team_id].append(person)
    result = []
    for team in teams:
        members = grouped.get(team.id, []) + monitor_pairs.get(team.id, [])
        pair_kind = "monitor_pair" if monitor_pairs.get(team.id) else "participants"
        result.append({
            "id": team.id, "name": team.name, "type": "competition",
            "member_source": pair_kind,
            "table_number": team.table_number,
            "monitor_person_id": (responsibility.get(team.id)
                                  if responsibility.get(team.id) in monitor_names else None),
            "monitor_name": monitor_names.get(responsibility.get(team.id)),
            "members": [person_record(p, team.name, team.table_number) for p in members],
            "checked_in": sum(1 for p in members if p.check_in_at),
            "first_check_in": min((p.check_in_at for p in members if p.check_in_at),
                                  default=None),
        })
    if sort == "check_in":
        result.sort(key=lambda x: (x["first_check_in"] is None,
                                  x["first_check_in"] or 0, x["name"].lower()))
    elif sort == "size":
        result.sort(key=lambda x: (-len(x["members"]), x["name"].lower()))
    else:
        result.sort(key=lambda x: x["name"].lower())
    monitors = [person_record(p) for p in people if p.kind == "monitor"]
    return {
        "teams": [{
            "id": "monitoring", "name": "Monitoring Team", "type": "monitoring",
            "members": sorted(monitors, key=lambda x: x["name"].lower()),
            "checked_in": sum(1 for p in monitors if p["check_in_at"]),
            "first_check_in": None,
        }, *result],
        "totals": {"people": len(people), "monitors": len(monitors),
                   "competition_teams": len(teams)},
    }


@router.post("/people", status_code=201)
def create_person(
    payload: PersonInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    existing = db.scalar(select(Person).where(
        Person.email == payload.email, Person.event_id == event.id
    ))
    if existing and existing.active:
        raise HTTPException(status_code=409, detail="This email already exists.")
    if existing:
        existing.name = payload.name.strip()
        existing.kind = payload.kind
        existing.active = True
        existing.team_id = team_id(db, payload.team_name, event.id) if (
            payload.kind == "participant"
        ) else None
        audit(db, ctx.staff.id, event.id, "person.reactivate", existing.id,
              {"kind": existing.kind})
        db.commit()
        return person_record(existing, payload.team_name)
    person = Person(
        event_id=event.id, name=payload.name.strip(), email=payload.email, kind=payload.kind,
        team_id=team_id(db, payload.team_name, event.id) if payload.kind == "participant" else None,
    )
    db.add(person)
    db.flush()
    audit(db, ctx.staff.id, event.id, "person.create", person.id, {"kind": person.kind})
    db.commit()
    return person_record(person, payload.team_name)


def enforce_native_role(
    db: Session, person: Person, desired_kind: str, *, reconcile: bool = False,
) -> bool:
    """Synchronize a joined member before changing their event role locally.

    Returns whether a remote write was required. Nothing is silently changed
    in Supervisely when a person has not joined yet.
    """
    if person.kind == desired_kind and not reconcile:
        return False
    if person.supervisely_user_id is None:
        if person.membership_status == "joined":
            raise HTTPException(
                status_code=409,
                detail="This joined member has no verified Supervisely ID. Refresh and link their account first.",
            )
        return False
    config = db.get(SuperviselyConfig, 1)
    if config is None or not config.team_id:
        raise HTTPException(status_code=409, detail="Configure the Supervisely team before editing this member.")
    target_role = "manager" if desired_kind == "monitor" else "annotator"
    original = person.supervisely_role
    if original == target_role and not reconcile:
        return False
    try:
        updated = change_member_role(
            config, user_id=person.supervisely_user_id,
            target_role=target_role, expected_role=original,
        )
    except NativeAPIError as exc:
        raise HTTPException(
            status_code=409 if exc.code in {
                "stale_role", "member_missing", "admin_protected", "role_unverified",
            } else 502,
            detail=str(exc),
        ) from exc
    person.supervisely_role = updated.role
    person.supervisely_login = updated.login
    return original != updated.role


def align_staff_access(db: Session, person: Person) -> None:
    """Participant role revokes monitoring access and existing sessions.

    A re-promoted person may recover their former Nightjar staff account only
    when it was disabled by this role conversion.
    """
    staff = db.scalar(select(StaffAccount).where(StaffAccount.email == person.email))
    if staff is None or "organiser" in staff.roles:
        return
    fields = dict(person.source_fields or {})
    if person.kind == "participant":
        if "monitor" in staff.roles:
            staff.active = False
            fields["nightjar_disabled_for_role"] = True
            db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
    elif fields.pop("nightjar_disabled_for_role", False):
        if "monitor" in staff.roles:
            staff.active = True
    person.source_fields = fields


@router.patch("/people/{person_id}")
def update_person(
    person_id: str, payload: PersonUpdate,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    person = db.scalar(select(Person).where(
        Person.id == person_id, Person.event_id == event.id,
    ).with_for_update())
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found.")
    fields = payload.model_fields_set
    desired_kind = payload.kind if "kind" in fields and payload.kind else person.kind
    if desired_kind != person.kind:
        privileged = db.scalar(select(StaffAccount).where(
            StaffAccount.email == person.email,
        ))
        if privileged is not None and "organiser" in privileged.roles:
            raise HTTPException(
                status_code=409, detail="The organiser account must retain its admin/monitor role.",
            )
    if "email" in fields and payload.email is not None and payload.email != person.email:
        if person.supervisely_user_id is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot change a verified Supervisely identity's email. Unlink it first.",
            )
        if db.scalar(select(Person).where(
            Person.email == payload.email, Person.event_id == event.id
        )):
            raise HTTPException(status_code=409, detail="This email already exists.")
    native_changed = enforce_native_role(db, person, desired_kind)
    if "email" in fields and payload.email is not None:
        person.email = payload.email
    if "name" in fields and payload.name is not None:
        person.name = payload.name.strip()
    if "kind" in fields and payload.kind is not None:
        person.kind = payload.kind
        if person.kind == "monitor":
            person.team_id = None
    if "team_name" in fields:
        person.team_id = (
            team_id(db, payload.team_name, event.id) if person.kind == "participant" else None
        )
    if "active" in fields and payload.active is not None:
        person.active = payload.active
    align_staff_access(db, person)
    audit(db, ctx.staff.id, event.id, "person.update", person.id, {
        "fields": sorted(fields), "supervisely_role_updated": native_changed,
        "native_user_id": person.supervisely_user_id,
    })
    db.commit()
    team = db.get(CompetitionTeam, person.team_id) if person.team_id else None
    return person_record(person, team.name if team else None)


@router.post("/people/{person_id}/supervisely-role")
def sync_person_supervisely_role(
    person_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    person = db.scalar(select(Person).where(
        Person.id == person_id, Person.event_id == event.id,
    ).with_for_update())
    if person is None or not person.active:
        raise HTTPException(status_code=404, detail="Active person not found.")
    native_changed = enforce_native_role(db, person, person.kind, reconcile=True)
    audit(db, ctx.staff.id, event.id, "person.supervisely.role.sync", person.id, {
        "kind": person.kind, "supervisely_role_updated": native_changed,
        "native_user_id": person.supervisely_user_id,
    })
    db.commit()
    return person_record(person)



class MonitorRegistrationApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


@router.put("/people/{person_id}/monitor-registration")
def change_monitor_registration_approval(
    person_id: str,
    payload: MonitorRegistrationApproval,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    person = db.scalar(select(Person).where(
        Person.id == person_id, Person.event_id == event.id,
    ).with_for_update())
    if person is None:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    if not person.active or person.kind != "monitor":
        raise HTTPException(status_code=409, detail="Registration approval requires an active monitor.")
    fields = dict(person.source_fields or {})
    # Set explicit allow/deny regardless of whether the monitor came from CSV.
    fields["monitor_registration_approved"] = payload.approved
    person.source_fields = fields
    audit(db, ctx.staff.id, event.id, "monitor.registration.approval",
          person.id, {"approved": payload.approved})
    db.commit()
    return person_record(person)



def parse_rows(payload: RosterPayload) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip("\ufeff"), newline=""))
        headers = reader.fieldnames or []
        expected = [payload.mapping.name, payload.mapping.email]
        expected += [h for h in (payload.mapping.kind, payload.mapping.team) if h]
        if len(set(expected)) != len(expected) or any(h not in headers for h in expected):
            raise ValueError("Select distinct, existing CSV columns for each mapped field.")
        rows = []
        used: set[str] = set()
        for raw in reader:
            if len(rows) >= 5000:
                raise ValueError("Import is limited to 5000 people at a time.")
            if None in raw:
                raise ValueError("A CSV row contains more values than its header.")
            name = str(raw.get(payload.mapping.name) or "").strip()
            email = normalized_email(str(raw.get(payload.mapping.email) or ""))
            if not name or len(name) > 200:
                raise ValueError("Every row must contain a name (max 200 characters).")
            if email in used:
                raise ValueError("Duplicate email within the imported CSV.")
            used.add(email)
            kind = payload.default_kind
            if payload.mapping.kind:
                source_kind = str(raw.get(payload.mapping.kind) or "").strip().lower()
                if source_kind in ("monitor", "monitors", "manager", "staff"):
                    kind = "monitor"
                elif source_kind in ("participant", "participants", "annotator"):
                    kind = "participant"
                elif source_kind:
                    raise ValueError(f"Unknown role value in the CSV: {source_kind[:40]}")
            team = str(raw.get(payload.mapping.team) or "").strip() if payload.mapping.team else ""
            if len(team) > 200:
                raise ValueError("A team name exceeds 200 characters.")
            rows.append({"name": name, "email": email, "kind": kind,
                         "team_name": team if kind == "participant" else None,
                         "source_fields": {str(k): str(v or "") for k, v in raw.items()}})
        if not rows:
            raise ValueError("The CSV contains no data rows.")
        return rows
    except (csv.Error, UnicodeError) as exc:
        raise ValueError("The supplied CSV could not be parsed.") from exc


def import_report(db: Session, rows: list[dict], apply: bool, actor: str,
                  event_id: str) -> dict:
    emails = [r["email"] for r in rows]
    existing = {
        p.email: p for p in db.scalars(select(Person).where(Person.email.in_(emails), Person.event_id == event_id))
    }
    teams = {t.id: t.name for t in db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id
    ))}
    created = changed = unchanged = 0
    # CSV imports must not silently diverge from a joined Supervisely role.
    # Per-person edits provide an atomic verified native role transition.
    for row in rows:
        existing_person = existing.get(row["email"])
        if (existing_person is not None and existing_person.kind != row["kind"]
                and (existing_person.supervisely_user_id is not None
                     or existing_person.membership_status == "joined")):
            raise ValueError(
                "CSV changes a joined person's role. Edit that person's role "
                "individually to synchronize Supervisely first."
            )
    for row in rows:
        found = existing.get(row["email"])
        desired = row["team_name"]
        current_team = teams.get(found.team_id) if found else None
        if found is None:
            created += 1
            if apply:
                db.add(Person(
                    event_id=event_id, name=row["name"], email=row["email"], kind=row["kind"],
                    team_id=team_id(db, desired, event_id) if row["kind"] == "participant" else None,
                    source_fields=row["source_fields"],
                ))
        elif ((found.name, found.kind, current_team, found.active) != (
            row["name"], row["kind"], desired or None, True
        ) or (found.source_fields or {}).get("source") == "supervisely_member_sync"):
            changed += 1
            if apply:
                found.active = True
                found.name = row["name"]
                # Preserve an explicit organiser approval/denial on reimport.
                prior = found.source_fields or {}
                found.source_fields = dict(row["source_fields"])
                if "monitor_registration_approved" in prior:
                    found.source_fields["monitor_registration_approved"] = (
                        prior["monitor_registration_approved"]
                    )
                found.kind = row["kind"]
                found.team_id = (
                    team_id(db, desired, event_id) if row["kind"] == "participant" else None
                )
        else:
            unchanged += 1
    if apply:
        audit(db, actor, event_id, "roster.import", None,
              {"rows": len(rows), "created": created, "changed": changed})
    return {
        "rows": len(rows), "created": created,
        "changed": changed, "unchanged": unchanged,
    }


class InvitationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/people/invitations-reported")
def report_native_invites(
    payload: InvitationReport,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    if len(set(payload.person_ids)) != len(payload.person_ids):
        raise HTTPException(status_code=422, detail="Duplicate recipient in selection.")
    event = current_event(db, ctx, writable=True)
    recipients = list(db.scalars(
        select(Person).where(Person.id.in_(payload.person_ids),
                             Person.event_id == event.id).with_for_update()
    ))
    if len(recipients) != len(payload.person_ids) or any(not p.active for p in recipients):
        raise HTTPException(status_code=422, detail="Some recipients no longer exist.")
    if len({p.kind for p in recipients}) > 1:
        raise HTTPException(status_code=422, detail="Invite one native role at a time.")
    for person in recipients:
        if person.membership_status == "not_invited":
            person.membership_status = "reported_sent"
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="invitation.reported_sent",
        target_type="invitation_batch", details={
            "count": len(recipients), "kind": recipients[0].kind,
            "person_ids": payload.person_ids,
            "native_verified": False,
        },
    ))
    db.commit()
    return {"recorded": len(recipients), "native_verified": False}


@router.post("/roster/preview")
def preview_roster(
    payload: RosterPayload,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    try:
        rows = parse_rows(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event = current_event(db, ctx)
    return import_report(db, rows, False, ctx.staff.id, event.id)


@router.post("/roster/import")
def import_roster(
    payload: RosterPayload,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    try:
        rows = parse_rows(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event = current_event(db, ctx, writable=True)
    report = import_report(db, rows, True, ctx.staff.id, event.id)
    digest = hashlib.sha256(payload.csv_text.encode("utf-8")).hexdigest()
    db.add(ImportBatch(event_id=event.id, source_hash=digest, staff_id=ctx.staff.id,
                       row_count=report["rows"], created_count=report["created"],
                       changed_count=report["changed"]))
    db.commit()
    return report
