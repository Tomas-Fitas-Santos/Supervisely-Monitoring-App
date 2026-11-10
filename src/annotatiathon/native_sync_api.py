"""Explicit, organiser-only read-only native synchronization with local confirmation.

Supervisely is only queried. Preview creates a short-lived local plan; applying it
updates Nightjar's membership labels without inviting, removing or changing remote roles.
"""
import hashlib
import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import AuditEvent, NativeSyncPlan, Person, SuperviselyConfig, epoch
from .supervisely_client import NativeAPIError, NativeMember, NativeTeamSnapshot, read_team_snapshot

router = APIRouter(prefix="/api/v1", tags=["native membership"])


def config_for_sync(db: Session) -> SuperviselyConfig:
    config = db.get(SuperviselyConfig, 1)
    if not config or not config.team_id:
        raise HTTPException(
            status_code=422, detail="Configure a Supervisely Team ID in Settings first."
        )
    return config


def read_or_fail(config: SuperviselyConfig, db: Session) -> NativeTeamSnapshot:
    try:
        snapshot = read_team_snapshot(config)
    except NativeAPIError as exc:
        config.verified = False
        config.last_verified_at = None
        config.last_error_code = exc.code
        db.commit()
        raise HTTPException(
            status_code=422 if exc.code in {"missing_token", "missing_team", "untrusted_host"}
            else 502,
            detail=str(exc),
        ) from exc
    config.verified = True
    config.last_verified_at = epoch()
    config.last_error_code = None
    db.flush()
    return snapshot


def source_digest(config: SuperviselyConfig, source: NativeTeamSnapshot) -> str:
    return hashlib.sha256(
        (config.server_url + "|" + source.digest()).encode()
    ).hexdigest()


def roster_digest(people: list[Person]) -> str:
    state = sorted(
        [(p.id, p.email, p.kind, p.active, p.supervisely_user_id,
          p.supervisely_login, p.membership_status) for p in people]
    )
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()


def all_people(db: Session, event_id: str) -> list[Person]:
    return list(db.scalars(
        select(Person).where(Person.event_id == event_id, Person.active.is_(True))
        .order_by(Person.id)
    ))


@router.post("/settings/supervisely/verify")
def verify_native_connection(
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    config = config_for_sync(db)
    result = read_or_fail(config, db)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, action="supervisely.connection.verify",
        target_type="supervisely_config", target_id="1",
        details={"team_id": result.team_id, "member_count": len(result.members),
                 "account_id": result.account_id},
    ))
    db.commit()
    return {
        "verified": True, "team_id": result.team_id, "team_name": result.team_name,
        "workspace_id": result.workspace_id, "account_id": result.account_id,
        "account_login": result.account_login, "member_count": len(result.members),
        "native_mutations": False,
    }


class ManualMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str
    native_user_id: int = Field(gt=0)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manual_links: list[ManualMatch] = Field(default_factory=list, max_length=500)


def proposed_role(person: Person, member: NativeMember) -> bool:
    return (person.kind == "participant" and member.role != "annotator") or (
        person.kind == "monitor" and member.role not in {"admin", "manager"}
    )


def association_proposal(
    members: tuple[NativeMember, ...], people: list[Person],
    manual: list[ManualMatch],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    by_id = {p.id: p for p in people}
    by_email = {p.email.lower(): p for p in people}
    existing_ids = {
        p.supervisely_user_id: p for p in people if p.supervisely_user_id is not None
    }
    existing_logins = {
        p.supervisely_login.casefold(): p for p in people if p.supervisely_login
    }
    if (len({m.person_id for m in manual}) != len(manual)
            or len({m.native_user_id for m in manual}) != len(manual)):
        raise HTTPException(status_code=422, detail="Each manual mapping must be unique.")
    remote = {m.id: m for m in members}
    links: dict[int, Person] = {}
    for item in manual:
        person = by_id.get(item.person_id)
        member = remote.get(item.native_user_id)
        if person is None or member is None:
            raise HTTPException(
                status_code=422,
                detail="Manual mapping must reference an active roster person and team member.",
            )
        if person.supervisely_user_id not in (None, member.id):
            raise HTTPException(
                status_code=409,
                detail="An existing verified native identity conflicts with the manual mapping.",
            )
        links[member.id] = person
    matches: list[dict] = []
    unmatched: list[dict] = []
    conflicts: list[dict] = []
    seen_people: set[str] = set()
    for member in members:
        person = links.get(member.id)
        method = "manual" if person else None
        if person is None:
            person = existing_ids.get(member.id)
            method = "verified_id" if person else None
        if person is None:
            person = existing_logins.get(member.login.casefold())
            method = "known_login" if person else None
        if person is None and member.email:
            person = by_email.get(member.email)
            method = "exact_email" if person else None
        if person is None:
            unmatched.append(asdict(member))
            continue
        if (person.id in seen_people or (
            person.supervisely_user_id not in (None, member.id)
        ) or (person.id in {p.id for uid, p in links.items() if uid != member.id})):
            conflicts.append({
                "native_user_id": member.id, "login": member.login,
                "reason": "One-to-one native identity conflict",
            })
            continue
        seen_people.add(person.id)
        matches.append({
            "person_id": person.id, "person_name": person.name,
            "person_email": person.email, "native_user_id": member.id,
            "native_login": member.login, "native_role": member.role,
            "method": method, "role_warning": proposed_role(person, member),
        })
    remaining = [{
        "person_id": p.id, "name": p.name, "email": p.email,
        "current_status": p.membership_status,
    } for p in people if p.id not in seen_people]
    return matches, unmatched, remaining, conflicts


@router.post("/people/supervisely/refresh")
def refresh_native_membership(
    ctx: AuthContext = Depends(requires(
        "organiser", "monitor", "reception", "activity", "distribution", "observer",
        mutation=True,
    )),
    db: Session = Depends(get_db),
):
    """Reconcile current native team members into this event's roster.

    Existing registrations are matched by verified ID/login or exact email.
    Native members without a roster record are added only when Supervisely
    provides a unique email and a recognisable member role. No app-login
    privileges, native invitations, remote roles, or teams are changed.
    """
    event = current_event(db, ctx)
    if event.status != "active":
        return {"status": "archived", "matched": 0, "updated": 0,
                "created": 0, "unmatched_native": 0, "unmatched_roster": 0,
                "role_warnings": 0, "conflicts": 0, "native_mutations": False}

    config = config_for_sync(db)
    source = read_or_fail(config, db)
    people = all_people(db, event.id)
    matched, unmatched, remaining, conflicts = association_proposal(
        source.members, people, []
    )
    # Do not partly apply a snapshot containing incompatible identity links.
    if conflicts:
        db.commit()
        return {"status": "needs_review", "matched": len(matched), "updated": 0,
                "created": 0, "unmatched_native": len(unmatched),
                "unmatched_roster": len(remaining),
                "role_warnings": sum(bool(m["role_warning"]) for m in matched),
                "conflicts": len(conflicts), "native_mutations": False}

    # Lock all event identities including inactive accounts; never silently
    # reactivate an intentionally deactivated registration.
    all_records = list(db.scalars(select(Person).where(
        Person.event_id == event.id,
    ).order_by(Person.id).with_for_update()))
    by_person_id = {p.id: p for p in all_records}
    used_emails = {p.email.casefold() for p in all_records}
    used_native_ids = {
        p.supervisely_user_id for p in all_records
        if p.supervisely_user_id is not None
    }
    now = epoch()
    updated = created = 0
    for item in matched:
        person = by_person_id.get(item["person_id"])
        if person is None or not person.active:
            raise HTTPException(status_code=409, detail="Roster changed; refresh again.")
        desired = (item["native_user_id"], item["native_login"],
                   item["native_role"], "joined")
        current = (person.supervisely_user_id, person.supervisely_login,
                   person.supervisely_role, person.membership_status)
        if current != desired:
            person.supervisely_user_id = item["native_user_id"]
            person.supervisely_login = item["native_login"]
            person.supervisely_role = item["native_role"]
            person.membership_status = "joined"
            updated += 1
        person.native_verified_at = now

    # No automatic role guessing from a username. A native account with no
    # email can be linked in the existing organiser review panel.
    valid_unmatched = []
    for member in unmatched:
        email = member.get("email")
        role = member.get("role")
        native_id = member["id"]
        if not email or "@" not in email or len(email) > 320:
            continue
        if native_id in used_native_ids or email.casefold() in used_emails:
            continue
        if role == "annotator":
            kind = "participant"
        elif role in {"admin", "manager", "developer"}:
            kind = "monitor"
        else:
            continue
        valid_unmatched.append((member, kind))

    # Emails or remote IDs occurring twice must not create arbitrary records.
    email_counts = {}
    for member, _ in valid_unmatched:
        key = member["email"].casefold()
        email_counts[key] = email_counts.get(key, 0) + 1
    created_emails = set()
    for member, kind in valid_unmatched:
        email = member["email"].casefold()
        if email_counts[email] != 1 or email in created_emails:
            continue
        # Keep unknown native-team members visible as unassigned; a participant
        # must still be placed into a two-person competition team explicitly.
        db.add(Person(
            event_id=event.id,
            email=email,
            name=member["login"],
            kind=kind,
            team_id=None,
            membership_status="joined",
            supervisely_user_id=member["id"],
            supervisely_login=member["login"],
            supervisely_role=member["role"],
            native_verified_at=now,
            source_fields={"source": "supervisely_member_sync"},
        ))
        created += 1
        created_emails.add(email)

    if updated or created:
        db.add(AuditEvent(
            staff_id=ctx.staff.id, event_id=event.id,
            action="supervisely.membership.auto_refresh", target_type="team",
            target_id=str(source.team_id),
            details={"updated": updated, "created": created,
                     "matched": len(matched),
                     "role_warnings": sum(bool(m["role_warning"]) for m in matched),
                     "native_mutations": False},
        ))
    db.commit()
    return {"status": "ok", "matched": len(matched), "updated": updated,
            "created": created, "unmatched_native": len(unmatched) - created,
            "unmatched_roster": len(remaining),
            "role_warnings": sum(bool(m["role_warning"]) for m in matched),
            "conflicts": 0, "native_mutations": False}


@router.post("/people/supervisely/preview")
def preview_native_sync(
    payload: PreviewRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    config = config_for_sync(db)
    source = read_or_fail(config, db)
    people = all_people(db, event.id)
    matched, unmatched, remaining, conflicts = association_proposal(
        source.members, people, payload.manual_links
    )
    plan = NativeSyncPlan(
        event_id=event.id, staff_id=ctx.staff.id, team_id=source.team_id,
        source_digest=source_digest(config, source), roster_digest=roster_digest(people),
        associations=matched, expires_at=epoch() + 600,
    )
    db.add(plan)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="supervisely.membership.preview", target_type="native_sync_plan",
        target_id=plan.id,
        details={
            "team_id": source.team_id, "matched": len(matched),
            "unmatched_native": len(unmatched), "role_warnings": sum(
                1 for item in matched if item["role_warning"]
            ),
        },
    ))
    db.commit()
    return {
        "plan_id": plan.id, "event_id": event.id, "team_id": source.team_id,
        "team_name": source.team_name, "expires_at": plan.expires_at,
        "members_total": len(source.members), "roster_total": len(people),
        "matched": matched, "unmatched_native": unmatched,
        "unmatched_roster": remaining, "conflicts": conflicts,
        "manual_links": [item.model_dump() for item in payload.manual_links],
        "remote_mutations": False,
        "notice": "Only positive, verified team membership is applied. "
                  "Unmatched people retain their existing local status.",
    }


@router.post("/people/supervisely/plans/{plan_id}/apply")
def apply_native_sync(
    plan_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(NativeSyncPlan).where(
        NativeSyncPlan.id == plan_id,
        NativeSyncPlan.event_id == event.id,
        NativeSyncPlan.staff_id == ctx.staff.id,
    ).with_for_update())
    if plan is None:
        raise HTTPException(status_code=404, detail="Membership preview not found.")
    if plan.applied_at is not None:
        return {"applied": len(plan.associations), "already_applied": True,
                "native_mutations": False}
    if plan.expires_at < epoch():
        raise HTTPException(status_code=409, detail="Membership preview expired.")
    config = config_for_sync(db)
    if config.team_id != plan.team_id:
        raise HTTPException(
            status_code=409, detail="Configured team changed. Generate a new preview."
        )
    people = all_people(db, event.id)
    if plan.roster_digest != roster_digest(people):
        raise HTTPException(status_code=409, detail="Roster changed. Preview again.")
    source = read_or_fail(config, db)
    if plan.source_digest != source_digest(config, source):
        raise HTTPException(
            status_code=409, detail="Native membership changed. Generate a new preview."
        )
    # Lock all matched recipients before writing. Never overwrite other events.
    ids = sorted({m["person_id"] for m in plan.associations})
    persons = {
        p.id: p for p in db.scalars(
            select(Person).where(Person.id.in_(ids), Person.event_id == event.id)
            .order_by(Person.id).with_for_update()
        )
    }
    if len(persons) != len(ids):
        raise HTTPException(status_code=409, detail="Roster changed. Preview again.")
    now = epoch()
    for assoc in plan.associations:
        person = persons[assoc["person_id"]]
        if not person.active:
            raise HTTPException(status_code=409, detail="Roster was deactivated.")
        person.supervisely_user_id = assoc["native_user_id"]
        person.supervisely_login = assoc["native_login"]
        person.supervisely_role = assoc["native_role"]
        person.native_verified_at = now
        person.membership_status = "joined"
    plan.applied_at = now
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="supervisely.membership.reconcile", target_type="native_sync_plan",
        target_id=plan.id,
        details={"team_id": plan.team_id, "matched_person_ids": ids,
                 "native_mutations": False},
    ))
    db.commit()
    return {"applied": len(ids), "already_applied": False,
            "native_mutations": False, "not_matched_unchanged": True}
