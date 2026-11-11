"""Event-local activities, ranked results and auditable prize allocations.

Awarding reserves ticket entitlements; staff issue physical tickets separately.
Finalisation is atomic, irreversible via this API and idempotent on retry.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .events_api import current_event
from .models import (
    Activity,
    ActivityAward,
    ActivityPrize,
    ActivityResult,
    AuditEvent,
    CompetitionTeam,
    Person,
    TicketCategory,
    TicketDistribution,
    TicketLedgerEntry,
    epoch,
)

router = APIRouter(prefix="/api/v1/activities", tags=["activities"])
VIEW_ROLES = ("organiser", "activity", "monitor", "reception", "distribution", "observer")
EDIT_ROLES = ("organiser", "monitor", "activity")


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    location: str = Field(default="", max_length=200)
    start_at: int | None = Field(default=None, gt=0)
    end_at: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_dates(self):
        if (self.start_at is not None and self.end_at is not None
                and self.end_at <= self.start_at):
            raise ValueError("Activity end must be after its start.")
        return self


class ActivityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=200)
    start_at: int | None = Field(default=None, gt=0)
    end_at: int | None = Field(default=None, gt=0)
    status: Literal["scheduled", "live", "completed", "cancelled"] | None = None


class ActivityResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: Literal["person", "team"]
    subject_id: str
    rank: int = Field(ge=1, le=10000)
    score_points: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    notes: str = Field(default="", max_length=3000)


class PrizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str
    min_rank: int = Field(ge=1, le=10000)
    max_rank: int = Field(ge=1, le=10000)
    quantity: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_rank_range(self):
        if self.max_rank < self.min_rank:
            raise ValueError("Final rank must not be lower than first rank.")
        return self


def activity_view(item: Activity) -> dict:
    return {
        "id": item.id, "event_id": item.event_id, "name": item.name,
        "description": item.description, "location": item.location,
        "start_at": item.start_at, "end_at": item.end_at,
        "status": item.status, "active": item.active,
        "awards_finalised_at": item.awards_finalised_at,
    }


def get_activity(db: Session, event_id: str, activity_id: str,
                 *, locked: bool = False) -> Activity:
    query = select(Activity).where(
        Activity.id == activity_id, Activity.event_id == event_id,
        Activity.active.is_(True),
    )
    if locked:
        query = query.with_for_update()
    activity = db.scalar(query)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found in selected event.")
    return activity


def ensure_editable(activity: Activity):
    if activity.awards_finalised_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Activity results and prize rules are frozen after finalisation.",
        )
    if activity.status == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled activity is not editable.")


@router.get("")
def list_activities(
    ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    items = db.scalars(select(Activity).where(
        Activity.event_id == event.id, Activity.active.is_(True)
    ).order_by(Activity.start_at, Activity.name)).all()
    return {"items": [activity_view(item) for item in items]}


@router.post("", status_code=201)
def create_activity(
    payload: ActivityCreate,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    item = Activity(
        event_id=event.id, name=payload.name.strip(),
        description=payload.description.strip(), location=payload.location.strip(),
        start_at=payload.start_at, end_at=payload.end_at,
        created_by=ctx.staff.id,
    )
    db.add(item)
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.create", target_type="activity",
        target_id=item.id, details={"name": item.name},
    ))
    db.commit()
    return activity_view(item)


@router.patch("/{activity_id}")
def edit_activity(
    activity_id: str,
    payload: ActivityUpdate,
    ctx: AuthContext = Depends(requires(*EDIT_ROLES, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    ensure_editable(activity)
    if "organiser" not in ctx.staff.roles and (
        payload.model_fields_set - {"status"}
    ):
        raise HTTPException(
            status_code=403,
            detail="Only organisers can change activity setup and scheduling.",
        )
    if payload.status == "cancelled" and "organiser" not in ctx.staff.roles:
        raise HTTPException(status_code=403, detail="Only organisers can cancel activities.")
    fields = payload.model_fields_set
    for field in fields:
        value = getattr(payload, field)
        if value is None and field not in ("start_at", "end_at"):
            raise HTTPException(status_code=422, detail="Field cannot be empty.")
        if field in ("name", "description", "location") and value is not None:
            value = value.strip()
        if field == "name" and not value:
            raise HTTPException(status_code=422, detail="Activity name is required.")
        setattr(activity, field, value)
    if (activity.start_at is not None and activity.end_at is not None
            and activity.end_at <= activity.start_at):
        raise HTTPException(status_code=422, detail="End must follow start.")
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id, action="activity.update",
        target_type="activity", target_id=activity.id,
        details={"fields": sorted(fields)},
    ))
    db.commit()
    return activity_view(activity)


@router.get("/{activity_id}")
def activity_details(
    activity_id: str,
    ctx: AuthContext = Depends(requires(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    activity = get_activity(db, event.id, activity_id)
    results = list(db.scalars(select(ActivityResult).where(
        ActivityResult.event_id == event.id,
        ActivityResult.activity_id == activity.id,
    ).order_by(ActivityResult.rank, ActivityResult.subject_type)))
    prizes = list(db.scalars(select(ActivityPrize).where(
        ActivityPrize.event_id == event.id,
        ActivityPrize.activity_id == activity.id,
    ).order_by(ActivityPrize.min_rank)))
    awards = list(db.scalars(select(ActivityAward).where(
        ActivityAward.event_id == event.id,
        ActivityAward.activity_id == activity.id,
    )))
    names = {
        p.id: p.name for p in db.scalars(select(Person).where(Person.event_id == event.id))
    }
    team_names = {
        t.id: t.name for t in db.scalars(select(CompetitionTeam).where(
            CompetitionTeam.event_id == event.id
        ))
    }
    categories = {
        c.id: c.name for c in db.scalars(select(TicketCategory).where(
            TicketCategory.event_id == event.id
        ))
    }
    return {
        "activity": activity_view(activity),
        "results": [{
            "id": r.id, "subject_type": r.subject_type, "subject_id": r.subject_id,
            "subject_name": (names if r.subject_type == "person" else team_names).get(
                r.subject_id, "Unknown"
            ),
            "rank": r.rank, "score_points": r.score_points, "notes": r.notes,
        } for r in results],
        "prizes": [{
            "id": p.id, "category_id": p.category_id,
            "category_name": categories.get(p.category_id, "Unknown"),
            "min_rank": p.min_rank, "max_rank": p.max_rank,
            "quantity": p.quantity,
        } for p in prizes],
        "awards": [{
            "id": award.id, "person_id": award.person_id,
            "person_name": names.get(award.person_id, "Unknown"),
            "prize_id": award.prize_id, "distribution_id": award.distribution_id,
            "awarded_at": award.awarded_at,
        } for award in awards],
    }


@router.put("/{activity_id}/results")
def record_result(
    activity_id: str, payload: ActivityResultInput,
    ctx: AuthContext = Depends(requires(*EDIT_ROLES, mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    ensure_editable(activity)
    if payload.subject_type == "person":
        subject = db.scalar(select(Person).where(
            Person.id == payload.subject_id, Person.event_id == event.id,
            Person.active.is_(True), Person.kind == "participant",
        ))
    else:
        subject = db.scalar(select(CompetitionTeam).where(
            CompetitionTeam.id == payload.subject_id,
            CompetitionTeam.event_id == event.id,
            CompetitionTeam.active.is_(True),
        ))
    if subject is None:
        raise HTTPException(status_code=422, detail="Selected participant/team is invalid.")
    result = db.scalar(select(ActivityResult).where(
        ActivityResult.activity_id == activity.id,
        ActivityResult.subject_type == payload.subject_type,
        ActivityResult.subject_id == payload.subject_id,
    ))
    created = result is None
    if result is None:
        result = ActivityResult(
            event_id=event.id, activity_id=activity.id,
            subject_type=payload.subject_type, subject_id=payload.subject_id,
            recorded_by=ctx.staff.id,
        )
        db.add(result)
    result.rank = payload.rank
    result.score_points = payload.score_points
    result.notes = payload.notes.strip()
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.result.record", target_type="activity_result",
        target_id=result.id, details={
            "activity_id": activity.id, "subject_type": result.subject_type,
            "subject_id": result.subject_id, "rank": result.rank,
            "created": created,
        },
    ))
    db.commit()
    return {
        "id": result.id, "rank": result.rank, "created": created,
        "subject_id": result.subject_id, "subject_type": result.subject_type,
    }


@router.delete("/{activity_id}/results/{result_id}", status_code=204)
def remove_result(
    activity_id: str, result_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    ensure_editable(activity)
    result = db.scalar(select(ActivityResult).where(
        ActivityResult.id == result_id,
        ActivityResult.activity_id == activity.id,
        ActivityResult.event_id == event.id,
    ))
    if result is None:
        raise HTTPException(status_code=404, detail="Result not found.")
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.result.delete", target_type="activity_result",
        target_id=result.id, details={
            "activity_id": activity.id, "subject_id": result.subject_id
        },
    ))
    db.delete(result)
    db.commit()


@router.post("/{activity_id}/prizes", status_code=201)
def add_prize(
    activity_id: str, payload: PrizeInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    ensure_editable(activity)
    category = db.get(TicketCategory, payload.category_id)
    if category is None or category.event_id != event.id or not category.active:
        raise HTTPException(status_code=422, detail="Choose an active event ticket category.")
    prize = ActivityPrize(
        event_id=event.id, activity_id=activity.id,
        category_id=category.id, min_rank=payload.min_rank,
        max_rank=payload.max_rank, quantity=payload.quantity,
        created_by=ctx.staff.id,
    )
    db.add(prize)
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.prize.create", target_type="activity_prize",
        target_id=prize.id, details={
            "activity_id": activity.id, "category_id": category.id,
            "rank_from": prize.min_rank, "rank_to": prize.max_rank,
            "quantity": prize.quantity,
        },
    ))
    db.commit()
    return {"id": prize.id, "category_id": category.id,
            "min_rank": prize.min_rank, "max_rank": prize.max_rank,
            "quantity": prize.quantity}


@router.delete("/{activity_id}/prizes/{prize_id}", status_code=204)
def remove_prize(
    activity_id: str, prize_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    ensure_editable(activity)
    prize = db.scalar(select(ActivityPrize).where(
        ActivityPrize.id == prize_id, ActivityPrize.activity_id == activity.id,
        ActivityPrize.event_id == event.id,
    ))
    if prize is None:
        raise HTTPException(status_code=404, detail="Prize rule not found.")
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.prize.delete", target_type="activity_prize",
        target_id=prize.id, details={"activity_id": activity.id},
    ))
    db.delete(prize)
    db.commit()


@router.post("/{activity_id}/finalise")
def finalise_activity(
    activity_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    activity = get_activity(db, event.id, activity_id, locked=True)
    if activity.awards_finalised_at is not None:
        return {
            "activity_id": activity.id, "already_finalised": True,
            "awards_finalised_at": activity.awards_finalised_at,
        }
    ensure_editable(activity)
    results = list(db.scalars(select(ActivityResult).where(
        ActivityResult.activity_id == activity.id, ActivityResult.event_id == event.id,
    )))
    if not results:
        raise HTTPException(
            status_code=409, detail="Record at least one ranked result before finalisation."
        )
    prizes = list(db.scalars(select(ActivityPrize).where(
        ActivityPrize.activity_id == activity.id, ActivityPrize.event_id == event.id,
    )))
    team_ids = [r.subject_id for r in results if r.subject_type == "team"]
    participants = list(db.scalars(select(Person).where(
        Person.event_id == event.id, Person.active.is_(True),
        Person.kind == "participant",
    )))
    team_members: dict[str, set[str]] = {}
    for person in participants:
        if person.team_id is not None and person.team_id in team_ids:
            team_members.setdefault(person.team_id, set()).add(person.id)
    eligible = {p.id for p in participants}
    created_awards = created_batches = 0
    for prize in prizes:
        category = db.get(TicketCategory, prize.category_id)
        if category is None or category.event_id != event.id or not category.active:
            raise HTTPException(
                status_code=409, detail="A prize references an inactive ticket category."
            )
        recipients: set[str] = set()
        for result in results:
            if prize.min_rank <= result.rank <= prize.max_rank:
                if result.subject_type == "person" and result.subject_id in eligible:
                    recipients.add(result.subject_id)
                elif result.subject_type == "team":
                    recipients.update(team_members.get(result.subject_id, set()))
        if not recipients:
            continue
        batch = TicketDistribution(
            event_id=event.id, category_id=category.id,
            operation="activity_award", quantity=prize.quantity,
            recipient_ids=sorted(recipients), scope="activity",
            status="confirmed", requested_by=ctx.staff.id,
            confirmed_by=ctx.staff.id, confirmed_at=epoch(),
        )
        db.add(batch)
        db.flush()
        created_batches += 1
        for person_id in sorted(recipients):
            db.add(TicketLedgerEntry(
                event_id=event.id, person_id=person_id,
                category_id=category.id, distribution_id=batch.id,
                kind="allocated", quantity=prize.quantity,
                staff_id=ctx.staff.id,
            ))
            db.add(ActivityAward(
                event_id=event.id, activity_id=activity.id,
                prize_id=prize.id, person_id=person_id,
                distribution_id=batch.id,
            ))
            created_awards += 1
    activity.status = "completed"
    activity.awards_finalised_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="activity.finalise", target_type="activity",
        target_id=activity.id,
        details={
            "results": len(results), "prize_rules": len(prizes),
            "awards": created_awards, "ticket_batches": created_batches,
        },
    ))
    db.commit()
    return {
        "activity_id": activity.id, "already_finalised": False,
        "awards_finalised_at": activity.awards_finalised_at,
        "prize_rules": len(prizes), "awards": created_awards,
        "ticket_batches": created_batches,
    }
