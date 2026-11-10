"""Deterministic morphology-aware, frame-weighted, video-atomic job planning.

A job is the pair-level unit of progress. It is not a claim that Supervisely
supports two annotators editing the same native answer simultaneously.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .db import get_db
from .datasets_api import get_dataset
from .events_api import current_event
from .models import (
    AuditEvent, CompetitionTeam, LabelingJob, LabelingPlan,
    MediaAsset, MonitorAssignment, Person, epoch,
)

router = APIRouter(prefix="/api/v1/labeling", tags=["labeling plans"])
VIEWERS = ("organiser", "monitor", "observer", "activity", "distribution", "reception")


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    jobs_per_team: int = Field(default=4, ge=1, le=100)
    redundancy: int = Field(default=3, ge=1, le=100)
    video_limit: int | None = Field(default=None, ge=0, le=100000)


@dataclass
class MediaUnit:
    id: str
    morphology: str
    video_group: str | None
    media_ids: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return len(self.media_ids)


@dataclass
class JobGroup:
    media_ids: list[str] = field(default_factory=list)
    frame_count: int = 0
    video_groups: set[str] = field(default_factory=set)
    morph_counts: Counter[str] = field(default_factory=Counter)


def build_units(assets: list[MediaAsset]) -> dict[str, list[MediaUnit]]:
    groups: dict[str, MediaUnit] = {}
    for item in assets:
        key = "v:" + item.video_group if item.video_group else "i:" + item.id
        if key not in groups:
            groups[key] = MediaUnit(id=key, morphology=item.morphology,
                                    video_group=item.video_group)
        groups[key].media_ids.append(item.id)
    categorized: dict[str, list[MediaUnit]] = defaultdict(list)
    for unit in groups.values():
        categorized[unit.morphology].append(unit)
    for units in categorized.values():
        units.sort(key=lambda u: (-u.weight, u.id))
    return categorized


def allocate(
    units: dict[str, list[MediaUnit]], teams: list[str],
    jobs_per_team: int, redundancy: int, video_limit: int | None,
) -> tuple[dict[str, list[JobGroup]], dict]:
    if not teams or redundancy > len(teams):
        raise HTTPException(
            status_code=422,
            detail="Redundancy cannot exceed the number of registered competition teams.",
        )
    if video_limit == 0 and any(u.video_group for group in units.values() for u in group):
        raise HTTPException(
            status_code=422, detail="Video limit zero cannot cover a dataset containing videos."
        )
    loads = Counter({team: 0 for team in teams})
    videos = Counter({team: 0 for team in teams})
    category_loads: dict[str, Counter[str]] = {
        t: Counter() for t in teams
    }
    selected: dict[str, list[MediaUnit]] = {t: [] for t in teams}
    category_names = sorted(units)
    ordered_units: list[MediaUnit] = []
    positions = {name: 0 for name in category_names}
    while True:
        progressed = False
        for name in category_names:
            index = positions[name]
            if index < len(units[name]):
                ordered_units.append(units[name][index])
                positions[name] += 1
                progressed = True
        if not progressed:
            break
    for unit in ordered_units:
        candidates = [
            t for t in teams
            if unit.video_group is None or video_limit is None or videos[t] < video_limit
        ]
        if len(candidates) < redundancy:
            raise HTTPException(
                status_code=422,
                detail="Video-per-team cap prevents the requested complete coverage. "
                       "Increase the cap or lower redundancy.",
            )
        chosen = sorted(
            candidates,
            key=lambda t: (category_loads[t][unit.morphology], loads[t], t),
        )[:redundancy]
        for team in chosen:
            selected[team].append(unit)
            loads[team] += unit.weight
            category_loads[team][unit.morphology] += unit.weight
            if unit.video_group is not None:
                videos[team] += 1
    result: dict[str, list[JobGroup]] = {}
    for team in teams:
        buckets = [JobGroup() for _ in range(jobs_per_team)]
        # Largest group first, distribute by frame count, never split a video.
        for unit in sorted(selected[team], key=lambda x: (-x.weight, x.id)):
            bucket = min(
                buckets, key=lambda g: (g.frame_count, len(g.video_groups), len(g.media_ids))
            )
            bucket.media_ids.extend(unit.media_ids)
            bucket.frame_count += unit.weight
            bucket.morph_counts[unit.morphology] += unit.weight
            if unit.video_group is not None:
                bucket.video_groups.add(unit.video_group)
        result[team] = [b for b in buckets if b.media_ids]
    totals = {
        "assets": sum(u.weight for units_by_morph in units.values() for u in units_by_morph),
        "assignment_copies": sum(loads.values()),
        "team_counts": dict(loads),
        "video_counts": dict(videos),
        "job_count": sum(len(x) for x in result.values()),
        "min_frames_per_team": min(loads.values()),
        "max_frames_per_team": max(loads.values()),
        "morphologies": category_names,
    }
    return result, totals


@router.post("/plans", status_code=201)
def create_plan(
    payload: PlanInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    dataset = get_dataset(db, event.id, payload.dataset_id)
    if dataset.status != "ready":
        raise HTTPException(status_code=409, detail="Finalize local dataset before planning.")
    existing = db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id, LabelingPlan.status == "approved"
    ))
    if existing:
        raise HTTPException(
            status_code=409, detail="Event has an approved labeling plan. "
                                    "Archive and create a new event to start another run.",
        )
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id, CompetitionTeam.active.is_(True),
    ).order_by(CompetitionTeam.name, CompetitionTeam.id)))
    if not teams:
        raise HTTPException(status_code=422, detail="Import competition teams first.")
    counts = dict(db.execute(select(
        Person.team_id, func.count(Person.id),
    ).where(
        Person.event_id == event.id, Person.active.is_(True),
        Person.kind == "participant", Person.team_id.is_not(None),
    ).group_by(Person.team_id)))
    if any(counts.get(team.id) != 2 for team in teams):
        raise HTTPException(
            status_code=422,
            detail="Every competition team must contain exactly two active participants.",
        )
    assets = list(db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == event.id, MediaAsset.dataset_id == dataset.id,
    ).order_by(MediaAsset.relative_path)))
    if not assets:
        raise HTTPException(status_code=422, detail="Dataset has no media assets.")
    groups, totals = allocate(
        build_units(assets), [t.id for t in teams], payload.jobs_per_team,
        payload.redundancy, payload.video_limit,
    )
    assignments = {
        m.team_id: m.monitor_person_id for m in db.scalars(select(MonitorAssignment).where(
            MonitorAssignment.event_id == event.id, MonitorAssignment.active.is_(True),
        ))
    }
    plan = LabelingPlan(
        event_id=event.id, dataset_id=dataset.id,
        jobs_per_team=payload.jobs_per_team, redundancy=payload.redundancy,
        video_limit=payload.video_limit, created_by=ctx.staff.id,
    )
    db.add(plan)
    db.flush()
    for team in teams:
        for seq, group in enumerate(groups[team.id], start=1):
            db.add(LabelingJob(
                event_id=event.id, dataset_id=dataset.id, plan_id=plan.id,
                team_id=team.id, sequence=seq,
                media_ids=group.media_ids, frame_count=group.frame_count,
                video_count=len(group.video_groups),
                morphology_counts=dict(group.morph_counts),
                assigned_monitor_id=assignments.get(team.id),
            ))
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.plan.create", target_type="labeling_plan",
        target_id=plan.id, details={
            "dataset_id": dataset.id, "redundancy": payload.redundancy,
            "jobs_per_team": payload.jobs_per_team, "summary": totals,
        },
    ))
    db.commit()
    return {"plan_id": plan.id, "status": plan.status, **totals}


@router.get("/plans")
def list_plans(
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    items = db.scalars(select(LabelingPlan).where(
        LabelingPlan.event_id == event.id,
    ).order_by(LabelingPlan.created_at.desc())).all()
    return {"items": [{
        "id": p.id, "dataset_id": p.dataset_id, "status": p.status,
        "jobs_per_team": p.jobs_per_team, "redundancy": p.redundancy,
        "video_limit": p.video_limit, "created_at": p.created_at,
        "approved_at": p.approved_at,
    } for p in items]}


@router.post("/plans/{plan_id}/approve")
def approve_plan(
    plan_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.id == plan_id, LabelingPlan.event_id == event.id,
    ).with_for_update())
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if plan.status == "approved":
        return {"id": plan.id, "status": "approved", "already_approved": True}
    if plan.status != "planned":
        raise HTTPException(status_code=409, detail="Plan is not approvable.")
    active = db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id, LabelingPlan.status == "approved",
    ))
    if active:
        raise HTTPException(status_code=409, detail="Another plan is already approved.")
    dataset = get_dataset(db, event.id, plan.dataset_id)
    if dataset.status != "ready":
        raise HTTPException(status_code=409, detail="Dataset is not ready.")
    plan.status = "approved"
    plan.approved_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.plan.approve", target_type="labeling_plan",
        target_id=plan.id, details={"dataset_id": plan.dataset_id},
    ))
    db.commit()
    return {"id": plan.id, "status": "approved", "already_approved": False}


@router.get("/jobs")
def list_jobs(
    plan_id: str | None = None,
    team_id: str | None = None,
    status: str | None = None,
    limit: int = 250,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=422, detail="Limit must be 1-2000.")
    query = select(LabelingJob).where(LabelingJob.event_id == event.id)
    if plan_id:
        query = query.where(LabelingJob.plan_id == plan_id)
    if team_id:
        query = query.where(LabelingJob.team_id == team_id)
    if status:
        query = query.where(LabelingJob.status == status)
    items = list(db.scalars(query.order_by(
        LabelingJob.team_id, LabelingJob.sequence,
    ).limit(limit)))
    teams = {t.id: t.name for t in db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id,
    ))}
    return {"items": [{
        "id": job.id, "plan_id": job.plan_id, "dataset_id": job.dataset_id,
        "team_id": job.team_id, "team_name": teams.get(job.team_id, "Unknown"),
        "sequence": job.sequence, "frame_count": job.frame_count,
        "video_count": job.video_count, "morphology_counts": job.morphology_counts,
        "status": job.status, "review_status": job.review_status,
        "assigned_monitor_id": job.assigned_monitor_id,
        "started_at": job.started_at, "submitted_at": job.submitted_at,
        "completed_at": job.completed_at,
    } for job in items]}


@router.get("/jobs/{job_id}/media")
def job_media(
    job_id: str, page: int = 1, size: int = 100,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.event_id == event.id, LabelingJob.id == job_id
    ))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if size < 1 or size > 200 or page < 1:
        raise HTTPException(status_code=422, detail="Pagination out of range.")
    total = len(job.media_ids)
    ids = job.media_ids[(page-1)*size:page*size]
    assets = {x.id: x for x in db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == event.id, MediaAsset.id.in_(ids)
    ))}
    return {
        "job_id": job.id, "total": total, "page": page, "size": size,
        "items": [{
            "id": assets[mid].id, "relative_path": assets[mid].relative_path,
            "morphology": assets[mid].morphology,
            "video_group": assets[mid].video_group,
            "native_image_id": assets[mid].native_image_id,
        } for mid in ids if mid in assets],
    }
