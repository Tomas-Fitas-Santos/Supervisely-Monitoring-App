"""Per-session event selection, archival, and non-destructive reset workflows.

Archived events stay available for read-only review. Reset previews are server stored
and validated against the exact current event state before a confirmed execution.
"""
import hashlib
import json
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .models import (
    DEFAULT_EVENT_ID,
    Activity,
    ActivityAward,
    ActivityPrize,
    ActivityResult,
    AuditEvent,
    CompetitionTeam,
    Event,
    EventSnapshot,
    LabelingJob,
    LabelingPlan,
    MonitorAssignment,
    Person,
    ResetPlan,
    TicketCategory,
    TicketDistribution,
    TicketLedgerEntry,
    epoch,
)

router = APIRouter(prefix="/api/v1/events", tags=["events"])
AREAS = ("attendance", "invitations", "tickets", "roster", "activities", "assignments")


def current_event(
    db: Session, ctx: AuthContext, *, writable: bool = False
) -> Event:
    event_id = ctx.session.event_id or DEFAULT_EVENT_ID
    statement = select(Event).where(Event.id == event_id)
    if writable:
        statement = statement.with_for_update()
    event = db.scalar(statement)
    if event is None:
        raise HTTPException(status_code=409, detail="Select an existing event first.")
    if writable and event.status != "active":
        raise HTTPException(status_code=409, detail="Archived events are read-only.")
    return event


def event_view(event: Event) -> dict:
    return {
        "id": event.id, "name": event.name, "status": event.status,
        "created_at": event.created_at, "archived_at": event.archived_at,
        "reset_from_id": event.reset_from_id,
        "start_at": event.start_at, "end_at": event.end_at,
        "timezone": "Europe/Lisbon",
    }


def append_audit(
    db: Session, staff_id: str, event_id: str, action: str,
    target: str | None = None, details: dict | None = None,
) -> None:
    db.add(AuditEvent(
        staff_id=staff_id, event_id=event_id, action=action,
        target_type="event", target_id=target or event_id, details=details or {},
    ))


def seed_categories(db: Session, event_id: str) -> None:
    for slug, name, threshold in (
        ("pizza", "Pizza", 2), ("drinks", "Drinks", 3),
        ("massage", "Massage", None),
    ):
        db.add(TicketCategory(
            event_id=event_id, slug=slug, name=name, active=True,
            warning_enabled=threshold is not None, warning_threshold=threshold,
            warning_window_minutes=60,
        ))


@router.get("")
def list_events(
    ctx: AuthContext = Depends(requires(
        "organiser", "monitor", "reception", "activity", "distribution", "observer"
    )),
    db: Session = Depends(get_db),
):
    events = list(db.scalars(select(Event).order_by(Event.created_at, Event.name)))
    return {
        "items": [event_view(event) for event in events],
        "current_event_id": ctx.session.event_id or DEFAULT_EVENT_ID,
    }


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=200)


@router.post("", status_code=201)
def create_event(
    payload: EventCreate,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if len(name) < 3:
        raise HTTPException(status_code=422, detail="Event name must contain 3 characters.")
    if db.scalar(select(Event.id).where(Event.name == name)):
        raise HTTPException(status_code=409, detail="An event with this name already exists.")
    event = Event(name=name)
    db.add(event)
    db.flush()
    seed_categories(db, event.id)
    append_audit(db, ctx.staff.id, event.id, "event.create")
    ctx.session.event_id = event.id
    db.commit()
    return event_view(event)


class EventSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: int = Field(gt=0, lt=2_147_483_647)
    end_at: int = Field(gt=0, lt=2_147_483_647)
    timezone: Literal["Europe/Lisbon"] = "Europe/Lisbon"


@router.put("/{event_id}/schedule")
def save_event_schedule(
    event_id: str,
    payload: EventSchedule,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    # Store unambiguous UTC instants. Browser forms explicitly convert from
    # Europe/Lisbon before reaching the API, including DST-gap validation.
    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=422, detail="Event end must follow its start.")
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    if event.status != "active":
        raise HTTPException(status_code=409, detail="Archived events are read-only.")
    from .models import Activity
    scheduled = db.scalars(select(Activity).where(
        Activity.event_id == event.id, Activity.active.is_(True),
    )).all()
    conflicts = [activity.name for activity in scheduled if (
        activity.start_at is not None and activity.start_at < payload.start_at
    ) or (
        activity.end_at is not None and activity.end_at > payload.end_at
    )]
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail="Existing activities fall outside this window: " + ", ".join(conflicts[:3]),
        )
    previous = {"start_at": event.start_at, "end_at": event.end_at}
    event.start_at = payload.start_at
    event.end_at = payload.end_at
    append_audit(db, ctx.staff.id, event.id, "event.schedule.update",
                 details={**previous, "new_start_at": payload.start_at,
                          "new_end_at": payload.end_at, "timezone": payload.timezone})
    db.commit()
    return event_view(event)


@router.post("/{event_id}/select")
def select_event(
    event_id: str,
    ctx: AuthContext = Depends(requires(
        "organiser", "monitor", "reception", "activity", "distribution", "observer",
        mutation=True,
    )),
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    ctx.session.event_id = event.id
    db.commit()
    return event_view(event)


class EventConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


@router.post("/{event_id}/archive")
def archive_event(
    event_id: str,
    payload: EventConfirm,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    if payload.confirmation != event.name:
        raise HTTPException(status_code=422, detail="Type the exact event name.")
    if event.status != "archived":
        event.status = "archived"
        event.archived_at = epoch()
        append_audit(db, ctx.staff.id, event.id, "event.archive")
        db.commit()
    return event_view(event)


@router.post("/{event_id}/reopen")
def reopen_event(
    event_id: str,
    payload: EventConfirm,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    if payload.confirmation != event.name:
        raise HTTPException(status_code=422, detail="Type the exact event name.")
    if event.status != "active":
        event.status = "active"
        event.archived_at = None
        append_audit(db, ctx.staff.id, event.id, "event.reopen")
        db.commit()
    return event_view(event)


def state_data(db: Session, event_id: str) -> dict:
    # This is a private local snapshot; never returned in an unauthorised preview.
    people = list(db.scalars(select(Person).where(
        Person.event_id == event_id
    ).order_by(Person.id)))
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id
    ).order_by(CompetitionTeam.id)))
    categories = list(db.scalars(select(TicketCategory).where(
        TicketCategory.event_id == event_id
    ).order_by(TicketCategory.id)))
    batches = list(db.scalars(select(TicketDistribution).where(
        TicketDistribution.event_id == event_id
    ).order_by(TicketDistribution.id)))
    ledger = list(db.scalars(select(TicketLedgerEntry).where(
        TicketLedgerEntry.event_id == event_id
    ).order_by(TicketLedgerEntry.id)))
    assignments = list(db.scalars(select(MonitorAssignment).where(
        MonitorAssignment.event_id == event_id
    ).order_by(MonitorAssignment.id)))
    activities = list(db.scalars(select(Activity).where(
        Activity.event_id == event_id
    ).order_by(Activity.id)))
    results = list(db.scalars(select(ActivityResult).where(
        ActivityResult.event_id == event_id
    ).order_by(ActivityResult.id)))
    prizes = list(db.scalars(select(ActivityPrize).where(
        ActivityPrize.event_id == event_id
    ).order_by(ActivityPrize.id)))
    awards = list(db.scalars(select(ActivityAward).where(
        ActivityAward.event_id == event_id
    ).order_by(ActivityAward.id)))
    return {
        "people": [{
            "id": p.id, "name": p.name, "email": p.email, "kind": p.kind,
            "team_id": p.team_id, "active": p.active,
            "membership_status": p.membership_status,
            "supervisely_user_id": p.supervisely_user_id,
            "supervisely_login": p.supervisely_login,
            "supervisely_role": p.supervisely_role,
            "native_verified_at": p.native_verified_at,
            "check_in_at": p.check_in_at, "table_number": p.table_number,
            "source_fields": p.source_fields,
        } for p in people],
        "teams": [{"id": t.id, "name": t.name, "active": t.active,
                   "table_number": t.table_number} for t in teams],
        "categories": [{
            "id": c.id, "slug": c.slug, "name": c.name, "active": c.active,
            "warning_enabled": c.warning_enabled,
            "warning_threshold": c.warning_threshold,
            "warning_window_minutes": c.warning_window_minutes,
        } for c in categories],
        "batches": [{
            "id": b.id, "status": b.status, "operation": b.operation,
            "quantity": b.quantity, "recipient_ids": b.recipient_ids,
            "category_id": b.category_id,
        } for b in batches],
        "ledger": [{
            "id": entry.id, "person_id": entry.person_id,
            "category_id": entry.category_id, "kind": entry.kind,
            "quantity": entry.quantity,
        } for entry in ledger],
        "monitor_assignments": [{
            "id": a.id, "team_id": a.team_id,
            "monitor_person_id": a.monitor_person_id, "active": a.active,
        } for a in assignments],
        "activities": [{
            "id": a.id, "name": a.name, "description": a.description,
            "location": a.location, "start_at": a.start_at, "end_at": a.end_at,
            "status": a.status, "active": a.active,
            "awards_finalised_at": a.awards_finalised_at,
        } for a in activities],
        "activity_results": [{
            "id": r.id, "activity_id": r.activity_id,
            "subject_type": r.subject_type, "subject_id": r.subject_id,
            "rank": r.rank, "score_points": r.score_points, "notes": r.notes,
        } for r in results],
        "activity_prizes": [{
            "id": p.id, "activity_id": p.activity_id,
            "category_id": p.category_id, "min_rank": p.min_rank,
            "max_rank": p.max_rank, "quantity": p.quantity,
        } for p in prizes],
        "activity_awards": [{
            "id": a.id, "activity_id": a.activity_id,
            "prize_id": a.prize_id, "person_id": a.person_id,
            "distribution_id": a.distribution_id,
        } for a in awards],
    }


def state_digest(state: dict) -> str:
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reset_summary(state: dict) -> dict:
    return {
        "people": sum(1 for p in state["people"] if p["active"]),
        "teams": sum(1 for t in state["teams"] if t["active"]),
        "checked_in": sum(1 for p in state["people"] if p["check_in_at"] is not None),
        "manual_invites": sum(
            1 for p in state["people"] if p["membership_status"] == "reported_sent"
        ),
        "ticket_batches": sum(1 for b in state["batches"] if b["status"] == "confirmed"),
        "ticket_ledger_entries": len(state["ledger"]),
        "monitor_assignments": sum(
            1 for a in state["monitor_assignments"] if a["active"]
        ),
        "activities": sum(1 for a in state["activities"] if a["active"]),
        "activity_results": len(state["activity_results"]),
        "activity_awards": len(state["activity_awards"]),
    }


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["selected", "fresh"]
    areas: list[Literal[
        "attendance", "invitations", "tickets", "roster", "activities", "assignments"
    ]] = Field(
        default_factory=list, max_length=6
    )


@router.post("/resets/preview")
def preview_reset(
    payload: ResetRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    if payload.mode == "selected" and (
        not payload.areas or len(set(payload.areas)) != len(payload.areas)
    ):
        raise HTTPException(status_code=422, detail="Select distinct reset areas.")
    if payload.mode == "fresh" and payload.areas:
        raise HTTPException(status_code=422, detail="Fresh start includes all event areas.")
    # Preserve all native job provenance when users reset event logistics.
    if (payload.mode == "selected" and "roster" in payload.areas
            and db.scalar(select(LabelingJob.id).where(LabelingJob.event_id == event.id))):
        raise HTTPException(
            status_code=409,
            detail="This event already has labeling jobs. Start a new event instead of "
                   "clearing the linked competition roster.",
        )
    if payload.mode == "fresh" and db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.status.in_(["running", "paused"]),
    )):
        raise HTTPException(
            status_code=409,
            detail="Cannot archive an event with active native release plans. "
                   "Finish/reconcile the jobs before starting fresh.",
        )
    state = state_data(db, event.id)
    if (payload.mode == "selected" and "roster" in payload.areas
            and "tickets" not in payload.areas
            and any(b["status"] == "confirmed" for b in state["batches"])):
        raise HTTPException(
            status_code=409,
            detail="Roster reset requires ticket reset in the same operation. "
                   "Previous ticket batches must remain traceable.",
        )
    if (payload.mode == "selected" and "tickets" in payload.areas
            and "activities" not in payload.areas and state["activity_awards"]):
        raise HTTPException(
            status_code=409,
            detail="Ticket reset must also reset activities when prizes have been awarded.",
        )
    if (payload.mode == "selected" and "activities" in payload.areas
            and "tickets" not in payload.areas and state["activity_awards"]):
        raise HTTPException(
            status_code=409,
            detail="Activity reset must also reset tickets when prizes have been awarded.",
        )
    if (payload.mode == "selected" and "roster" in payload.areas
            and "activities" not in payload.areas and state["activity_results"]):
        raise HTTPException(
            status_code=409,
            detail="Roster reset requires activity reset when ranked results exist.",
        )
    selected = list(AREAS) if payload.mode == "fresh" else payload.areas
    plan = ResetPlan(
        event_id=event.id, staff_id=ctx.staff.id, mode=payload.mode,
        areas=selected, digest=state_digest(state),
        summary=reset_summary(state), expires_at=epoch() + 10 * 60,
    )
    db.add(plan)
    db.commit()
    return {
        "plan_id": plan.id, "event_id": event.id, "event_name": event.name,
        "mode": plan.mode, "areas": selected, "counts": plan.summary,
        "expires_at": plan.expires_at, "confirmation_phrase": "RESET " + event.name,
        "notes": [
            "No deletion of remote Supervisely data is performed.",
            "Staff accounts and shared connection settings remain untouched.",
            "Fresh start archives the original event and creates a separate empty event.",
            "Selective reset stores a local PostgreSQL snapshot and preserves audit history.",
            "A PostgreSQL snapshot is not an off-machine backup. Export it separately.",
        ],
    }


class ResetConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


def reset_ticket_balances(db: Session, event_id: str, staff_id: str) -> None:
    # Cancel all unconfirmed previews so they cannot be confirmed after reset.
    previews = db.scalars(select(TicketDistribution).where(
        TicketDistribution.event_id == event_id,
        TicketDistribution.status == "preview",
    )).all()
    for item in previews:
        item.status = "cancelled"
    totals: dict[tuple[str, str, str], int] = defaultdict(int)
    for item in db.scalars(select(TicketLedgerEntry).where(
        TicketLedgerEntry.event_id == event_id
    )):
        totals[(item.category_id, item.person_id, item.kind)] += item.quantity
    # One compensating batch per category with net balances. This is append only.
    categories = sorted({cid for cid, _, _ in totals})
    for category_id in categories:
        recipients = sorted({
            pid for cid, pid, kind in totals
            if cid == category_id and totals[(cid, pid, kind)]
        })
        if not recipients:
            continue
        batch = TicketDistribution(
            event_id=event_id, category_id=category_id, operation="reset",
            quantity=1, recipient_ids=recipients, scope="system",
            status="confirmed", requested_by=staff_id, confirmed_by=staff_id,
            confirmed_at=epoch(),
        )
        db.add(batch)
        db.flush()
        for (cid, pid, kind), amount in totals.items():
            if cid == category_id and amount:
                db.add(TicketLedgerEntry(
                    event_id=event_id, category_id=cid, person_id=pid,
                    distribution_id=batch.id, kind=kind, quantity=-amount,
                    staff_id=staff_id,
                ))


def reset_selected(db: Session, event_id: str, actor: str, areas: list[str]) -> None:
    if "tickets" in areas:
        reset_ticket_balances(db, event_id, actor)
    if "attendance" in areas:
        for team in db.scalars(select(CompetitionTeam).where(
            CompetitionTeam.event_id == event_id,
        )):
            team.table_number = None
        for person in db.scalars(select(Person).where(
            Person.event_id == event_id, Person.check_in_at.is_not(None)
        )):
            person.check_in_at = None
            person.table_number = None
    if "invitations" in areas:
        for person in db.scalars(select(Person).where(
            Person.event_id == event_id, Person.membership_status == "reported_sent"
        )):
            person.membership_status = "not_invited"
    if "activities" in areas:
        # Soft-archive activities, preserving all ranked results and award evidence.
        for activity in db.scalars(select(Activity).where(
            Activity.event_id == event_id, Activity.active.is_(True)
        )):
            activity.active = False
    if "assignments" in areas or "roster" in areas:
        for mapping in db.scalars(select(MonitorAssignment).where(
            MonitorAssignment.event_id == event_id,
            MonitorAssignment.active.is_(True),
        )):
            mapping.active = False
    if "roster" in areas:
        # Archive rather than delete: old ledger and audit identities remain intact.
        for person in db.scalars(select(Person).where(Person.event_id == event_id)):
            person.active = False
            person.check_in_at = None
            # Native verified membership remains represented in the historical record.
        for team in db.scalars(select(CompetitionTeam).where(
            CompetitionTeam.event_id == event_id
        )):
            team.active = False
        for batch in db.scalars(select(TicketDistribution).where(
            TicketDistribution.event_id == event_id,
            TicketDistribution.status == "preview",
        )):
            batch.status = "cancelled"


@router.post("/resets/{plan_id}/execute")
def execute_reset(
    plan_id: str,
    payload: ResetConfirmation,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(ResetPlan).where(ResetPlan.id == plan_id).with_for_update())
    if plan is None or plan.event_id != event.id or plan.staff_id != ctx.staff.id:
        raise HTTPException(status_code=404, detail="Reset preview not found for this event.")
    if plan.executed_at is not None:
        raise HTTPException(status_code=409, detail="Reset has already been executed.")
    if plan.expires_at < epoch():
        raise HTTPException(status_code=409, detail="Preview expired; generate a new one.")
    if payload.confirmation != "RESET " + event.name:
        raise HTTPException(status_code=422, detail="Type the exact confirmation phrase.")
    # Repeat the critical job guard at execution, as launch may occur after preview.
    if (plan.mode == "selected" and "roster" in plan.areas
            and db.scalar(select(LabelingJob.id).where(LabelingJob.event_id == event.id))):
        raise HTTPException(status_code=409, detail="Event jobs exist; do not reset roster.")
    if plan.mode == "fresh" and db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.status.in_(["running", "paused"]),
    )):
        raise HTTPException(status_code=409, detail="Active native plan prevents fresh reset.")
    before = state_data(db, event.id)
    if plan.digest != state_digest(before):
        raise HTTPException(
            status_code=409, detail="Event records changed. Preview the reset again."
        )
    snapshot = EventSnapshot(
        event_id=event.id, reset_plan_id=plan.id, snapshot_json=before,
    )
    db.add(snapshot)
    db.flush()
    now = epoch()
    plan.executed_at = now
    if plan.mode == "fresh":
        # Recoverability is intrinsic: the previous event remains unchanged and readable.
        event.status = "archived"
        event.archived_at = now
        new_event = Event(
            name=(event.name[:145] + " · Restart " + str(now) + "-" + plan.id[:6]),
            reset_from_id=event.id,
        )
        db.add(new_event)
        db.flush()
        seed_categories(db, new_event.id)
        ctx.session.event_id = new_event.id
        append_audit(db, ctx.staff.id, event.id, "event.fresh_start",
                     details={"new_event_id": new_event.id, "snapshot_id": snapshot.id})
        append_audit(db, ctx.staff.id, new_event.id, "event.create_from_reset",
                     details={"previous_event_id": event.id})
        db.commit()
        return {
            "mode": "fresh", "event": event_view(new_event),
            "archived_event_id": event.id, "snapshot_id": snapshot.id,
        }
    reset_selected(db, event.id, ctx.staff.id, plan.areas)
    db.flush()
    append_audit(db, ctx.staff.id, event.id, "event.reset_selected",
                 details={"areas": plan.areas, "snapshot_id": snapshot.id,
                          "before": plan.summary})
    db.commit()
    return {
        "mode": "selected", "event": event_view(event),
        "areas": plan.areas, "snapshot_id": snapshot.id,
    }


@router.get("/{event_id}/snapshots")
def list_snapshots(
    event_id: str,
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    if db.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    return {"items": [{
        "id": item.id, "event_id": item.event_id, "created_at": item.created_at,
        "reset_plan_id": item.reset_plan_id,
    } for item in db.scalars(select(EventSnapshot).where(
        EventSnapshot.event_id == event_id
    ).order_by(EventSnapshot.created_at.desc()).limit(100))]}


@router.get("/{event_id}/snapshots/{snapshot_id}")
def export_snapshot(
    event_id: str, snapshot_id: str,
    ctx: AuthContext = Depends(requires("organiser")),
    db: Session = Depends(get_db),
):
    item = db.get(EventSnapshot, snapshot_id)
    if not item or item.event_id != event_id or db.get(Event, event_id) is None:
        raise HTTPException(status_code=404, detail="Snapshot not found.")
    return {
        "format": "nightjar.event-snapshot.v1", "event_id": event_id,
        "snapshot_id": item.id, "created_at": item.created_at,
        "records": item.snapshot_json,
        "warning": "Contains personal data. Store encrypted and restrict access.",
    }
