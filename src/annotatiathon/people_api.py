"""Roster and team management. No Supervisely request runs in these endpoints."""
import csv
import hashlib
import io
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, normalized_email, requires
from .db import get_db
from .models import AuditEvent, CompetitionTeam, ImportBatch, Person

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


def team_id(db: Session, team_name: str | None) -> str | None:
    name = (team_name or "").strip()
    if not name:
        return None
    team = db.scalar(select(CompetitionTeam).where(CompetitionTeam.name == name))
    if team is None:
        team = CompetitionTeam(name=name)
        db.add(team)
        db.flush()
    return team.id


def person_record(p: Person, team: str | None = None) -> dict:
    return {
        "id": p.id, "name": p.name, "email": p.email, "kind": p.kind,
        "team_id": p.team_id, "team_name": team, "active": p.active,
        "membership_status": p.membership_status,
        "supervisely_login": p.supervisely_login,
        "check_in_at": p.check_in_at,
    }


def audit(db: Session, actor: str, action: str, target: str | None, details: dict) -> None:
    db.add(AuditEvent(staff_id=actor, action=action, target_type="person",
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
    query = select(Person).where(Person.active.is_(True))
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
    names = {t.id: t.name for t in db.scalars(select(CompetitionTeam))}
    return {
        "items": [person_record(p, names.get(p.team_id)) for p in people],
        "total": total, "page": page, "size": size,
    }


@router.get("/teams")
def list_teams(
    sort: Literal["name", "check_in", "size"] = "name",
    _ctx: AuthContext = Depends(requires(*VIEWER)),
    db: Session = Depends(get_db),
):
    teams = list(db.scalars(select(CompetitionTeam)))
    people = list(db.scalars(select(Person).where(Person.active.is_(True))))
    grouped: dict[str | None, list[Person]] = defaultdict(list)
    for person in people:
        grouped[person.team_id if person.kind == "participant" else None].append(person)
    result = []
    for team in teams:
        members = grouped.get(team.id, [])
        result.append({
            "id": team.id, "name": team.name, "type": "competition",
            "members": [person_record(p, team.name) for p in members],
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
    if db.scalar(select(Person).where(Person.email == payload.email)):
        raise HTTPException(status_code=409, detail="This email already exists.")
    person = Person(
        name=payload.name.strip(), email=payload.email, kind=payload.kind,
        team_id=team_id(db, payload.team_name) if payload.kind == "participant" else None,
    )
    db.add(person)
    db.flush()
    audit(db, ctx.staff.id, "person.create", person.id, {"kind": person.kind})
    db.commit()
    return person_record(person, payload.team_name)


@router.patch("/people/{person_id}")
def update_person(
    person_id: str, payload: PersonUpdate,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found.")
    fields = payload.model_fields_set
    if "email" in fields and payload.email is not None and payload.email != person.email:
        if db.scalar(select(Person).where(Person.email == payload.email)):
            raise HTTPException(status_code=409, detail="This email already exists.")
        person.email = payload.email
    if "name" in fields and payload.name is not None:
        person.name = payload.name.strip()
    if "kind" in fields and payload.kind is not None:
        person.kind = payload.kind
        if person.kind == "monitor":
            person.team_id = None
    if "team_name" in fields:
        person.team_id = (
            team_id(db, payload.team_name) if person.kind == "participant" else None
        )
    if "active" in fields and payload.active is not None:
        person.active = payload.active
    audit(db, ctx.staff.id, "person.update", person.id, {"fields": sorted(fields)})
    db.commit()
    team = db.get(CompetitionTeam, person.team_id) if person.team_id else None
    return person_record(person, team.name if team else None)


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


def import_report(db: Session, rows: list[dict], apply: bool, actor: str) -> dict:
    emails = [r["email"] for r in rows]
    existing = {
        p.email: p for p in db.scalars(select(Person).where(Person.email.in_(emails)))
    }
    teams = {t.id: t.name for t in db.scalars(select(CompetitionTeam))}
    created = changed = unchanged = 0
    for row in rows:
        found = existing.get(row["email"])
        desired = row["team_name"]
        current_team = teams.get(found.team_id) if found else None
        if found is None:
            created += 1
            if apply:
                db.add(Person(
                    name=row["name"], email=row["email"], kind=row["kind"],
                    team_id=team_id(db, desired) if row["kind"] == "participant" else None,
                    source_fields=row["source_fields"],
                ))
        elif (found.name, found.kind, current_team) != (row["name"], row["kind"], desired or None):
            changed += 1
            if apply:
                found.name = row["name"]
                found.source_fields = row["source_fields"]
                found.kind = row["kind"]
                found.team_id = (
                    team_id(db, desired) if row["kind"] == "participant" else None
                )
        else:
            unchanged += 1
    if apply:
        audit(db, actor, "roster.import", None,
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
    recipients = list(db.scalars(
        select(Person).where(Person.id.in_(payload.person_ids)).with_for_update()
    ))
    if len(recipients) != len(payload.person_ids) or any(not p.active for p in recipients):
        raise HTTPException(status_code=422, detail="Some recipients no longer exist.")
    if len({p.kind for p in recipients}) > 1:
        raise HTTPException(status_code=422, detail="Invite one native role at a time.")
    for person in recipients:
        if person.membership_status == "not_invited":
            person.membership_status = "reported_sent"
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="invitation.reported_sent",
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
    return import_report(db, rows, False, ctx.staff.id)


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
    report = import_report(db, rows, True, ctx.staff.id)
    digest = hashlib.sha256(payload.csv_text.encode("utf-8")).hexdigest()
    db.add(ImportBatch(source_hash=digest, staff_id=ctx.staff.id,
                       row_count=report["rows"], created_count=report["created"],
                       changed_count=report["changed"]))
    db.commit()
    return report
