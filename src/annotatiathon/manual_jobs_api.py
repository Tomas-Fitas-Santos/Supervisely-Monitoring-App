"""Manual video-to-annotator assignments without recreating an active event.

A human can stage a job in Nightjar or explicitly enqueue it for the native
Supervisely worker. The HTTP API never writes directly to Supervisely.
"""
from collections import Counter, defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .datasets_api import get_dataset
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    Person,
)
from .native_operations_api import native_ready, queue_publish
from .pair_members import pair_members

router = APIRouter(prefix="/api/v1/labeling/manual", tags=["manual labeling jobs"])


def videos_for_dataset(db: Session, event_id: str, dataset_id: str) -> dict[str, list[MediaAsset]]:
    assets = list(db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == event_id,
        MediaAsset.dataset_id == dataset_id,
        MediaAsset.video_group.is_not(None),
    ).order_by(MediaAsset.relative_path)))
    grouped: dict[str, list[MediaAsset]] = defaultdict(list)
    for asset in assets:
        if asset.video_group:
            grouped[asset.video_group].append(asset)
    for frames in grouped.values():
        frames.sort(key=lambda a: (a.frame_index if a.frame_index is not None else 10**12,
                                   a.relative_path))
    return grouped


def roster(db: Session, event_id: str) -> dict[str, list[Person]]:
    teams = list(db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id,
        CompetitionTeam.active.is_(True),
    ).order_by(CompetitionTeam.name, CompetitionTeam.id)))
    return {team.id: pair_members(db, event_id, team.id)[0] for team in teams}


@router.get("/options")
def manual_options(
    dataset_id: str,
    ctx: AuthContext = Depends(requires("organiser", "monitor")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    if ds.status != "ready":
        raise HTTPException(status_code=409, detail="Dataset must be finalized.")
    teams = roster(db, event.id)
    team_names = {t.id: t.name for t in db.scalars(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event.id, CompetitionTeam.active.is_(True)
    ))}
    videos = videos_for_dataset(db, event.id, dataset_id)
    existing = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event.id, LabelingJob.dataset_id == ds.id,
    )))
    assignments = []
    for job in existing:
        for group, frames in videos.items():
            if frames and frames[0].id in job.media_ids:
                assignments.append({
                    "team_id": job.team_id, "video_group": group,
                    "assignee_person_id": job.assignee_person_id,
                    "job_id": job.id, "status": job.status,
                })
    return {
        "teams": [
            {"id": team_id, "name": team_names[team_id],
             "members": [{"id": p.id, "name": p.name,
                          "native_ready": bool(p.supervisely_user_id
                              and p.membership_status == "joined" and p.native_verified_at
                              and (p.supervisely_role or "").lower() in
                              ({"annotator"} if p.kind == "participant"
                               else {"admin", "manager", "developer"}))}
                         for p in members]}
            for team_id, members in teams.items() if len(members) == 2
        ],
        "videos": [
            {"video_group": group, "frames": len(frames),
             "morphology": frames[0].morphology}
            for group, frames in sorted(videos.items())
        ],
        "existing": assignments,
    }


class ManualAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str
    assignee_person_id: str
    video_group: str = Field(min_length=1, max_length=500)


class ManualJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    assignments: list[ManualAssignment] = Field(min_length=1, max_length=100)
    dispatch: Literal["hold", "publish"] = "hold"


@router.post("/jobs", status_code=201)
def create_manual_jobs(
    payload: ManualJobRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    # Serialize manual writes so simultaneous requests cannot double-assign a video.
    db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    dataset = get_dataset(db, event.id, payload.dataset_id)
    if dataset.status != "ready":
        raise HTTPException(status_code=409, detail="Dataset must be finalized.")
    if payload.dispatch == "publish":
        native_ready(db)
    videos = videos_for_dataset(db, event.id, dataset.id)
    if not videos:
        raise HTTPException(status_code=422, detail="Dataset contains no videos.")
    teams = roster(db, event.id)
    existing = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event.id,
        LabelingJob.dataset_id == dataset.id,
    )))
    used: set[tuple[str, str]] = set()
    for job in existing:
        frames_in_job = set(job.media_ids)
        for group, frames in videos.items():
            if any(frame.id in frames_in_job for frame in frames):
                used.add((job.team_id, group))
    validated: list[tuple[ManualAssignment, list[MediaAsset]]] = []
    for assignment in payload.assignments:
        frames = videos.get(assignment.video_group)
        if not frames:
            raise HTTPException(status_code=422, detail="Selected video is not in this dataset.")
        members = teams.get(assignment.team_id, [])
        if len(members) != 2:
            raise HTTPException(status_code=422, detail="Selected team needs exactly two members.")
        person = next((p for p in members if p.id == assignment.assignee_person_id), None)
        if person is None:
            raise HTTPException(status_code=422, detail="Assignee is not a member of the selected team.")
        if payload.dispatch == "publish" and not (
            person.supervisely_user_id and person.membership_status == "joined"
            and person.native_verified_at
            and (person.supervisely_role or "").lower() in
            ({"annotator"} if person.kind == "participant"
             else {"admin", "manager", "developer"})
        ):
            raise HTTPException(
                status_code=409,
                detail="The selected annotator must have a verified Supervisely account.",
            )
        key = (assignment.team_id, assignment.video_group)
        if key in used:
            raise HTTPException(
                status_code=409,
                detail="This team already has an assignment for the selected video.",
            )
        used.add(key)
        validated.append((assignment, frames))
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.dataset_id == dataset.id,
        LabelingPlan.status == "running",
    ).order_by(LabelingPlan.created_at.desc()).limit(1))
    if plan is None:
        plan = LabelingPlan(
            event_id=event.id, dataset_id=dataset.id,
            jobs_per_team=0, redundancy=1, status="running",
            created_by=ctx.staff.id,
        )
        db.add(plan)
        db.flush()
    all_plan_jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.plan_id == plan.id, LabelingJob.event_id == event.id,
    )))
    sequences = Counter()
    for job in all_plan_jobs:
        sequences[job.team_id] = max(sequences[job.team_id], job.sequence)
    ids = []
    for assignment, frames in validated:
        sequences[assignment.team_id] += 1
        job = LabelingJob(
            event_id=event.id, dataset_id=dataset.id,
            plan_id=plan.id, team_id=assignment.team_id,
            sequence=sequences[assignment.team_id],
            assignee_person_id=assignment.assignee_person_id,
            release_mode=("manual_hold" if payload.dispatch == "hold" else "manual_released"),
            media_ids=[frame.id for frame in frames], frame_count=len(frames),
            video_count=1, morphology_counts=dict(Counter(
                frame.morphology for frame in frames
            )),
        )
        db.add(job)
        db.flush()
        if payload.dispatch == "publish":
            queue_publish(db, job)
        ids.append(job.id)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.jobs.manual.create",
        target_type="labeling_plan", target_id=plan.id,
        details={"job_ids": ids, "dispatch": payload.dispatch,
                 "video_count": len(ids)},
    ))
    db.commit()
    return {"job_ids": ids, "count": len(ids), "plan_id": plan.id,
            "dispatch": payload.dispatch,
            "message": ("Queued for the Supervisely worker."
                        if payload.dispatch == "publish"
                        else "Saved in Nightjar. Publish when ready.")}


@router.post("/jobs/{job_id}/publish")
def publish_manual_job(
    job_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    native_ready(db)
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.event_id == event.id, LabelingJob.id == job_id,
    ).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.release_mode != "manual_hold" or job.status != "queued":
        raise HTTPException(status_code=409, detail="Only held manual jobs can be released.")
    plan = db.get(LabelingPlan, job.plan_id)
    if plan is None or plan.status != "running":
        raise HTTPException(status_code=409, detail="The job's plan is not running.")
    members = roster(db, event.id).get(job.team_id, [])
    person = next((p for p in members if p.id == job.assignee_person_id), None)
    if (person is None or not person.supervisely_user_id
            or person.membership_status != "joined" or not person.native_verified_at):
        raise HTTPException(status_code=409, detail="Annotator is not verified in Supervisely.")
    job.release_mode = "manual_released"
    task = queue_publish(db, job)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.jobs.manual.publish", target_type="labeling_job",
        target_id=job.id, details={"operation_id": task.id},
    ))
    db.commit()
    return {"job_id": job.id, "status": "queued_for_supervisely",
            "operation_id": task.id}
