"""Audited ticket ledger: allocated, physically issued and redeemed are distinct."""
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    Person,
    TicketCategory,
    TicketDistribution,
    TicketLedgerEntry,
    epoch,
)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
VIEW_ROLES = ("organiser", "distribution", "reception", "activity", "monitor", "observer")
ACTION_ROLES = ("organiser", "monitor", "distribution", "reception")


class TicketCategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")


class TicketCategoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    active: bool | None = None
    warning_enabled: bool | None = None
    warning_threshold: int | None = Field(default=None, ge=1, le=100000)
    warning_window_minutes: int | None = Field(default=None, ge=1, le=10080)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["individuals", "teams", "all_checked_in", "all_registered"]
    person_ids: list[str] = Field(default_factory=list, max_length=5000)
    team_ids: list[str] = Field(default_factory=list, max_length=5000)


class DistributionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str
    operation: Literal["allocate", "issue", "redeem"]
    quantity: int = Field(ge=1, le=100)
    selection: Selection


def category_view(category: TicketCategory) -> dict:
    return {
        "id": category.id, "slug": category.slug, "name": category.name,
        "active": category.active, "warning_enabled": category.warning_enabled,
        "warning_threshold": category.warning_threshold,
        "warning_window_minutes": category.warning_window_minutes,
    }


@router.get("/categories")
def categories(
    _ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    return {"items": [
        category_view(item) for item in db.scalars(
            select(TicketCategory).where(TicketCategory.event_id == event.id)
            .order_by(TicketCategory.name)
        )
    ]}


@router.post("/categories", status_code=201)
def create_category(
    payload: TicketCategoryInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    if db.scalar(select(TicketCategory).where(
        TicketCategory.slug == payload.slug, TicketCategory.event_id == event.id
    )):
        raise HTTPException(status_code=409, detail="Category slug already exists.")
    category = TicketCategory(
        event_id=event.id, slug=payload.slug, name=payload.name.strip()
    )
    db.add(category)
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="tickets.category.create",
        target_type="ticket_category", target_id=category.id,
        details={"slug": category.slug},
    ))
    db.commit()
    return category_view(category)


@router.patch("/categories/{category_id}")
def edit_category(
    category_id: str,
    payload: TicketCategoryChange,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    category = db.get(TicketCategory, category_id)
    if category is None or category.event_id != event.id:
        raise HTTPException(status_code=404, detail="Ticket category not found.")
    for field in payload.model_fields_set:
        setattr(category, field, getattr(payload, field))
    if category.warning_enabled and category.warning_threshold is None:
        raise HTTPException(status_code=422, detail="Enabled warning requires a threshold.")
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="tickets.category.update",
        target_type="ticket_category", target_id=category.id,
        details={"changed": sorted(payload.model_fields_set)},
    ))
    db.commit()
    return category_view(category)


def resolve_people(db: Session, selection: Selection, event_id: str) -> list[Person]:
    scope = selection.scope
    if scope in ("individuals", "teams"):
        ids = selection.person_ids if scope == "individuals" else selection.team_ids
        if not ids or len(set(ids)) != len(ids):
            raise HTTPException(status_code=422, detail="Select distinct recipients or teams.")
    if scope == "individuals":
        stmt = select(Person).where(Person.id.in_(selection.person_ids))
    elif scope == "teams":
        stmt = select(Person).where(
            Person.kind == "participant", Person.team_id.in_(selection.team_ids)
        )
        count = db.scalar(select(func.count(CompetitionTeam.id)).where(
            CompetitionTeam.id.in_(selection.team_ids),
            CompetitionTeam.event_id == event_id,
            CompetitionTeam.active.is_(True),
        )) or 0
        if count != len(selection.team_ids):
            raise HTTPException(status_code=422, detail="One or more teams do not exist.")
    elif scope == "all_checked_in":
        stmt = select(Person).where(Person.check_in_at.is_not(None))
    else:
        stmt = select(Person)
    people = list(db.scalars(stmt.where(
        Person.active.is_(True), Person.event_id == event_id
    ).order_by(Person.id)))
    if scope == "individuals" and len(people) != len(selection.person_ids):
        raise HTTPException(status_code=422, detail="Some recipients are no longer active.")
    if not people:
        raise HTTPException(status_code=422, detail="No active recipients found.")
    if len(people) > 5000:
        raise HTTPException(status_code=422, detail="A batch can contain at most 5000 people.")
    return people


def balances(db: Session, event_id: str, category_id: str,
             ids: list[str]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"allocated": 0, "issued": 0, "redeemed": 0}
    )
    if not ids:
        return totals
    for person_id, kind, quantity in db.execute(
        select(TicketLedgerEntry.person_id, TicketLedgerEntry.kind,
               func.sum(TicketLedgerEntry.quantity)).where(
            TicketLedgerEntry.event_id == event_id,
            TicketLedgerEntry.category_id == category_id,
            TicketLedgerEntry.person_id.in_(ids),
        ).group_by(TicketLedgerEntry.person_id, TicketLedgerEntry.kind)
    ):
        totals[person_id][kind] = int(quantity)
    return totals


def warning_recipients(
    db: Session, category: TicketCategory, ids: list[str], requested: int
) -> list[str]:
    if not ids or not category.warning_enabled or category.warning_threshold is None:
        return []
    cutoff = epoch() - category.warning_window_minutes * 60
    recent = {
        key: int(value) for key, value in db.execute(
            select(TicketLedgerEntry.person_id,
                   func.sum(TicketLedgerEntry.quantity)).where(
                TicketLedgerEntry.event_id == category.event_id,
                TicketLedgerEntry.category_id == category.id,
                TicketLedgerEntry.person_id.in_(ids),
                TicketLedgerEntry.kind == "issued",
                TicketLedgerEntry.created_at >= cutoff,
            ).group_by(TicketLedgerEntry.person_id)
        )
    }
    return [
        pid for pid in ids
        if recent.get(pid, 0) + requested > category.warning_threshold
    ]


def snapshot_response(
    db: Session, batch: TicketDistribution, category: TicketCategory
) -> dict:
    people = list(db.scalars(
        select(Person).where(
            Person.id.in_(batch.recipient_ids), Person.event_id == batch.event_id
        ).order_by(Person.name)
    ))
    flagged = (
        warning_recipients(db, category, batch.recipient_ids, batch.quantity)
        if batch.operation == "issue" and batch.status == "preview" else []
    )
    return {
        "id": batch.id, "status": batch.status, "operation": batch.operation,
        "quantity": batch.quantity, "scope": batch.scope,
        "category": category_view(category),
        "count": len(batch.recipient_ids),
        "recipients": [{"id": p.id, "name": p.name, "email": p.email} for p in people],
        "soft_warning_ids": flagged,
        "soft_warning_count": len(flagged),
        "confirmed_at": batch.confirmed_at,
        "reversed_at": batch.reversed_at,
    }


@router.post("/preview")
def preview_distribution(
    payload: DistributionRequest,
    ctx: AuthContext = Depends(requires(*ACTION_ROLES, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    category = db.get(TicketCategory, payload.category_id)
    if category is None or category.event_id != event.id or (
        not category.active and payload.operation != "redeem"
    ):
        raise HTTPException(status_code=422, detail="Select an active ticket category.")
    people = resolve_people(db, payload.selection, event.id)
    batch = TicketDistribution(
        event_id=event.id, category_id=category.id, operation=payload.operation,
        quantity=payload.quantity, recipient_ids=[p.id for p in people],
        scope=payload.selection.scope, requested_by=ctx.staff.id,
    )
    db.add(batch)
    db.commit()
    return snapshot_response(db, batch, category)


def assert_redeem_available(ledger_totals: dict[str, int], qty: int) -> None:
    if ledger_totals["issued"] - ledger_totals["redeemed"] < qty:
        raise HTTPException(status_code=409, detail=(
            "Not enough physically issued tickets for at least one recipient. "
            "Refresh the preview and review the balances."
        ))


def insert_ledger(
    db: Session, batch: TicketDistribution, pid: str, kind: str,
    qty: int, staff_id: str, *, reversal_of: str | None = None
) -> None:
    db.add(TicketLedgerEntry(
        event_id=batch.event_id, person_id=pid, category_id=batch.category_id, distribution_id=batch.id,
        kind=kind, quantity=qty, staff_id=staff_id, reversal_of=reversal_of,
    ))


@router.post("/batches/{batch_id}/confirm")
def confirm_distribution(
    batch_id: str,
    ctx: AuthContext = Depends(requires(*ACTION_ROLES, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    batch = db.scalar(select(TicketDistribution).where(
        TicketDistribution.id == batch_id,
        TicketDistribution.event_id == event.id,
    ).with_for_update())
    if batch is None:
        raise HTTPException(status_code=404, detail="Distribution preview not found.")
    category = db.get(TicketCategory, batch.category_id)
    if batch.status == "confirmed":
        return snapshot_response(db, batch, category) | {"already_confirmed": True}
    if batch.status != "preview":
        raise HTTPException(status_code=409, detail="Batch is no longer confirmable.")
    if batch.created_at + 30 * 60 < epoch():
        raise HTTPException(status_code=409, detail="Preview expired; create a new one.")
    if not category.active and batch.operation != "redeem":
        raise HTTPException(status_code=409, detail="Category has been disabled.")
    # Lock recipient rows in stable order: two simultaneous ticket allocations cannot
    # overspend a person's outstanding balance or issue duplicate grants.
    people = list(db.scalars(
        select(Person).where(
            Person.id.in_(batch.recipient_ids), Person.event_id == event.id
        ).order_by(Person.id).with_for_update()
    ))
    if len(people) != len(batch.recipient_ids) or any(not p.active for p in people):
        raise HTTPException(status_code=409, detail="Roster changed; create a new preview.")
    current = balances(db, event.id, category.id, batch.recipient_ids)
    # Warnings are advisory, never a blocker, even when thresholds are exceeded.
    warned = warning_recipients(
        db, category, batch.recipient_ids, batch.quantity
    ) if batch.operation == "issue" else []
    for person in people:
        amounts = current[person.id]
        if batch.operation == "allocate":
            insert_ledger(db, batch, person.id, "allocated", batch.quantity, ctx.staff.id)
        elif batch.operation == "issue":
            deficit = max(0, amounts["issued"] + batch.quantity - amounts["allocated"])
            if deficit:
                insert_ledger(db, batch, person.id, "allocated", deficit, ctx.staff.id)
            insert_ledger(db, batch, person.id, "issued", batch.quantity, ctx.staff.id)
        else:
            assert_redeem_available(amounts, batch.quantity)
            insert_ledger(db, batch, person.id, "redeemed", batch.quantity, ctx.staff.id)
    batch.status = "confirmed"
    batch.confirmed_by = ctx.staff.id
    batch.confirmed_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action=f"tickets.{batch.operation}",
        target_type="ticket_distribution", target_id=batch.id,
        details={
            "category_id": category.id, "quantity": batch.quantity,
            "recipient_ids": batch.recipient_ids, "warning_count": len(warned),
        },
    ))
    db.commit()
    return snapshot_response(db, batch, category) | {
        "already_confirmed": False, "soft_warning_count": len(warned),
        "soft_warning_ids": warned,
    }


@router.post("/batches/{batch_id}/reverse")
def reverse_distribution(
    batch_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    batch = db.scalar(select(TicketDistribution).where(
        TicketDistribution.id == batch_id,
        TicketDistribution.event_id == event.id,
    ).with_for_update())
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if batch.status == "reversed":
        return {"id": batch.id, "status": "reversed", "already_reversed": True}
    if batch.status != "confirmed":
        raise HTTPException(status_code=409, detail="Only confirmed batches can be reversed.")
    if batch.operation == "activity_award":
        raise HTTPException(
            status_code=409,
            detail="Activity prize allocations require an activity-aware reset; "
                   "do not reverse their ticket batches independently.",
        )
    entries = list(db.scalars(
        select(TicketLedgerEntry).where(
            TicketLedgerEntry.distribution_id == batch.id,
            TicketLedgerEntry.event_id == event.id,
        ).order_by(TicketLedgerEntry.id)
    ))
    if not entries:
        raise HTTPException(status_code=409, detail="No ledger entries for this batch.")
    # Lock every affected person before checking balance invariants.
    ids = sorted(set(batch.recipient_ids))
    db.execute(select(Person.id).where(
        Person.id.in_(ids), Person.event_id == event.id
    ).order_by(Person.id).with_for_update()).all()
    current = balances(db, event.id, batch.category_id, ids)
    for entry in entries:
        current[entry.person_id][entry.kind] -= entry.quantity
    if any(
        row["allocated"] < row["issued"] or
        row["issued"] < row["redeemed"] or
        row["redeemed"] < 0
        for row in current.values()
    ):
        raise HTTPException(
            status_code=409, detail="Later ticket operations depend on this batch; "
            "reverse dependent operations first.",
        )
    for entry in entries:
        insert_ledger(
            db, batch, entry.person_id, entry.kind, -entry.quantity,
            ctx.staff.id, reversal_of=entry.id,
        )
    batch.status = "reversed"
    batch.reversed_at = epoch()
    batch.reversal_staff_id = ctx.staff.id
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="tickets.batch.reverse",
        target_type="ticket_distribution", target_id=batch.id,
        details={"recipient_ids": batch.recipient_ids},
    ))
    db.commit()
    return {"id": batch.id, "status": "reversed", "already_reversed": False}


@router.get("/balances")
def ticket_balances(
    person_id: str | None = None,
    _ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    query = select(
        TicketLedgerEntry.person_id, TicketLedgerEntry.category_id,
        TicketLedgerEntry.kind, func.sum(TicketLedgerEntry.quantity),
    )
    query = query.where(TicketLedgerEntry.event_id == event.id)
    if person_id:
        query = query.where(TicketLedgerEntry.person_id == person_id)
    query = query.group_by(
        TicketLedgerEntry.person_id, TicketLedgerEntry.category_id,
        TicketLedgerEntry.kind,
    )
    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"allocated": 0, "issued": 0, "redeemed": 0}
    )
    for pid, cid, kind, qty in db.execute(query):
        grouped[(pid, cid)][kind] = int(qty)
    return {"items": [
        {
            "person_id": pid, "category_id": cid, **entry,
            "allocated_remaining": entry["allocated"] - entry["issued"],
            "issued_remaining": entry["issued"] - entry["redeemed"],
        }
        for (pid, cid), entry in grouped.items()
    ]}


@router.get("/batches")
def list_batches(
    limit: int = Query(default=30, ge=1, le=100),
    _ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, _ctx)
    category_names = {c.id: c.name for c in db.scalars(select(TicketCategory).where(
        TicketCategory.event_id == event.id
    ))}
    batches = db.scalars(select(TicketDistribution).where(
        TicketDistribution.event_id == event.id,
        TicketDistribution.status != "preview"
    ).order_by(TicketDistribution.confirmed_at.desc()).limit(limit)).all()
    return {"items": [{
        "id": b.id, "category": category_names.get(b.category_id),
        "operation": b.operation, "quantity": b.quantity,
        "recipients": len(b.recipient_ids), "status": b.status,
        "confirmed_at": b.confirmed_at,
    } for b in batches]}
