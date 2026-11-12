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
    MediaVideo,
    MonitorAssignment,
    MonitorPairMember,
    NativeJobBinding,
    NativeOperation,
    Person,
    WorkBlock,
    epoch,
)
from .native_operations_api import native_identity_assignable, native_ready, queue_publish
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
    reserved_ids = {asset_id for job in existing for asset_id in job.media_ids}
    reserved_ids.update(asset_id for block in db.scalars(
        select(WorkBlock).join(LabelingPlan, LabelingPlan.id == WorkBlock.plan_id)
        .where(WorkBlock.event_id == event.id,
               LabelingPlan.dataset_id == ds.id,
               LabelingPlan.status.in_(("planned", "approved", "running", "paused")))
    ) for asset_id in block.media_ids)
    blocks = []
    for group, frames in sorted(videos.items()):
        for offset in range(0, len(frames), 30):
            window = frames[offset:offset + 30]
            if len(window) != 30 or any(
                frame.frame_index is None or frame.frame_index != window[0].frame_index + i
                for i, frame in enumerate(window)
            ):
                continue
            blocks.append({
                "video_group": group, "block_ordinal": offset // 30 + 1,
                "first_frame": window[0].frame_index,
                "last_frame": window[-1].frame_index,
                "frames": 30, "morphology": window[0].morphology,
                "reserved": any(frame.id in reserved_ids for frame in window),
            })
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
                          "native_ready": native_identity_assignable(p)}
                         for p in members]}
            for team_id, members in teams.items() if len(members) == 2
        ],
        "videos": [
            {"video_group": group, "frames": len(frames),
             "morphology": frames[0].morphology}
            for group, frames in sorted(videos.items())
        ],
        "blocks": blocks,
        "existing": assignments,
    }


class ManualAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_id: str
    assignee_person_id: str
    video_group: str = Field(min_length=1, max_length=500)
    block_ordinal: int | None = Field(default=None, ge=1)


class ManualJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    assignments: list[ManualAssignment] = Field(min_length=1, max_length=100)
    dispatch: Literal["hold", "publish"] = "publish"
    high_priority: bool = False
    allow_duplicates: bool = False


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
    if payload.dispatch == "hold" and payload.high_priority:
        raise HTTPException(
            status_code=422, detail="Priority applies only to queued jobs.",
        )
    videos = videos_for_dataset(db, event.id, dataset.id)
    if not videos:
        raise HTTPException(status_code=422, detail="Dataset contains no videos.")
    teams = roster(db, event.id)
    # Never allow accidental overlaps. Deliberate manual repeats require
    # allow_duplicates=True; an automatic plan can never create overlaps.
    all_existing = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event.id,
    )))
    used = set(db.scalars(select(MediaVideo.video_group).join(
        WorkBlock, WorkBlock.video_id == MediaVideo.id,
    ).join(LabelingPlan, LabelingPlan.id == WorkBlock.plan_id).where(
        WorkBlock.event_id == event.id, LabelingPlan.dataset_id == dataset.id,
        LabelingPlan.status.in_(["planned", "approved", "running", "paused"]),
    ).distinct()))
    for job in all_existing:
        if job.dataset_id != dataset.id:
            continue
        frame_ids = set(job.media_ids)
        for group, frames in videos.items():
            if frames and frames[0].id in frame_ids:
                used.add(group)
    used_ids = {mid for job in all_existing if job.dataset_id == dataset.id
                for mid in job.media_ids}
    used_ids.update(mid for block in db.scalars(
        select(WorkBlock).join(LabelingPlan, LabelingPlan.id == WorkBlock.plan_id)
        .where(WorkBlock.event_id == event.id,
               LabelingPlan.dataset_id == dataset.id,
               LabelingPlan.status.in_(("planned", "approved", "running", "paused")))
    ) for mid in block.media_ids)
    validated: list[tuple[ManualAssignment, list[MediaAsset]]] = []
    for assignment in payload.assignments:
        source = videos.get(assignment.video_group)
        if not source:
            raise HTTPException(status_code=422, detail="Selected video is not in this dataset.")
        if assignment.block_ordinal is None:
            # Historical whole-video API requests remain readable and valid.
            frames = source
        else:
            offset = (assignment.block_ordinal - 1) * 30
            frames = source[offset:offset + 30]
            if len(frames) != 30 or any(
                frame.frame_index is None or frame.frame_index != frames[0].frame_index + i
                for i, frame in enumerate(frames)
            ):
                raise HTTPException(
                    status_code=422, detail="Choose an existing consecutive 30-frame segment.",
                )
        members = teams.get(assignment.team_id, [])
        if len(members) != 2:
            raise HTTPException(status_code=422, detail="Selected team needs exactly two members.")
        person = next((p for p in members if p.id == assignment.assignee_person_id), None)
        if person is None:
            raise HTTPException(status_code=422, detail="Assignee is not a member of the selected team.")
        if payload.dispatch == "publish" and not native_identity_assignable(person):
            raise HTTPException(
                status_code=409,
                detail="The selected annotator must have a verified Supervisely account.",
            )
        conflict = (assignment.video_group in used if assignment.block_ordinal is None
                    else any(frame.id in used_ids for frame in frames))
        if conflict and not payload.allow_duplicates:
            raise HTTPException(
                status_code=409,
                detail="This video segment is already reserved. Explicitly allow "
                       "duplicates to repeat an existing segment.",
            )
        used.add(assignment.video_group)
        used_ids.update(frame.id for frame in frames)
        validated.append((assignment, frames))
    plan = db.scalar(select(LabelingPlan).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.dataset_id == dataset.id,
        LabelingPlan.status == "running",
        LabelingPlan.queue_seed.is_(None),
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
            queue_publish(db, job, high_priority=payload.high_priority)
        ids.append(job.id)
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.jobs.manual.create",
        target_type="labeling_plan", target_id=plan.id,
        details={"job_ids": ids, "dispatch": payload.dispatch,
                 "high_priority": payload.high_priority,
                 "allow_duplicates": payload.allow_duplicates,
                 "video_count": len(ids)},
    ))
    db.commit()
    return {"job_ids": ids, "count": len(ids), "plan_id": plan.id,
            "dispatch": payload.dispatch,
            "priority": "higher" if payload.high_priority else "lower",
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
    if person is None or not native_identity_assignable(person):
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


class ReassignJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str


def target_team(db: Session, event_id: str, person: Person) -> CompetitionTeam:
    team_id = person.team_id
    if not team_id:
        team_id = db.scalar(select(MonitorPairMember.team_id).where(
            MonitorPairMember.event_id == event_id,
            MonitorPairMember.person_id == person.id,
            MonitorPairMember.active.is_(True),
        ))
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.event_id == event_id,
        CompetitionTeam.id == team_id,
        CompetitionTeam.active.is_(True),
    ))
    if team is None:
        raise HTTPException(
            status_code=409, detail="New annotator must belong to an active team."
        )
    return team


def apply_local_reassignment(db: Session, job: LabelingJob,
                             person: Person, team: CompetitionTeam) -> None:
    if job.team_id != team.id:
        # Sequence is only unique within a team's labeling plan. Carry the
        # job into the recipient team without overwriting historical jobs.
        used = list(db.scalars(select(LabelingJob.sequence).where(
            LabelingJob.event_id == job.event_id,
            LabelingJob.plan_id == job.plan_id,
            LabelingJob.team_id == team.id,
        )))
        job.sequence = max(used, default=0) + 1
        job.team_id = team.id
    job.assignee_person_id = person.id
    job.assigned_monitor_id = db.scalar(select(
        MonitorAssignment.monitor_person_id,
    ).where(
        MonitorAssignment.event_id == job.event_id,
        MonitorAssignment.team_id == team.id,
        MonitorAssignment.active.is_(True),
    ))


@router.get("/assignees")
def reassignment_options(
    ctx: AuthContext = Depends(requires("organiser", "monitor")),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    people = list(db.scalars(select(Person).where(
        Person.event_id == event.id, Person.active.is_(True),
    ).order_by(Person.name, Person.email)))
    options = []
    for person in people:
        try:
            team = target_team(db, event.id, person)
        except HTTPException:
            continue
        options.append({
            "id": person.id, "name": person.name, "team_id": team.id,
            "team_name": team.name,
            "native_ready": native_identity_assignable(person),
        })
    return {"items": options}


@router.post("/jobs/{job_id}/reassign", status_code=202)
def reassign_job(
    job_id: str,
    payload: ReassignJobRequest,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.event_id == event.id,
        LabelingJob.id == job_id,
    ).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.release_mode in {"shared_primary", "shared_repeat"}:
        raise HTTPException(status_code=409, detail="Shared queue jobs retain their original claim.")
    if job.assignee_person_id is None:
        raise HTTPException(status_code=409, detail="Legacy shared jobs cannot be reassigned.")
    if job.status in {"on_review", "completed", "needs_correction"}:
        raise HTTPException(
            status_code=409,
            detail="A submitted or reviewed job cannot be reassigned. "
                   "Use a new manual job for another annotation attempt.",
        )
    person = db.scalar(select(Person).where(
        Person.event_id == event.id, Person.id == payload.person_id,
        Person.active.is_(True),
    ))
    if person is None:
        raise HTTPException(status_code=404, detail="New annotator not found.")
    team = target_team(db, event.id, person)
    if not native_identity_assignable(person):
        raise HTTPException(
            status_code=409, detail="New annotator must be verified in Supervisely.",
        )
    if person.id == job.assignee_person_id:
        return {"status": "unchanged", "job_id": job.id}
    publishing = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "publish_job",
        NativeOperation.target_id == job.id,
    ))
    if (job.status == "publishing" or
            (publishing is not None and publishing.status in {"running", "attention"})):
        raise HTTPException(
            status_code=409,
            detail="Job publishing has already begun or requires reconciliation. "
                   "Finish or retry its Supervisely publication before reassigning.",
        )
    has_native = db.scalar(select(NativeJobBinding.id).where(
        NativeJobBinding.event_id == event.id,
        NativeJobBinding.job_id == job.id,
    )) is not None
    if not has_native and job.status == "queued":
        old = {"team_id": job.team_id, "person_id": job.assignee_person_id}
        apply_local_reassignment(db, job, person, team)
        db.add(AuditEvent(
            staff_id=ctx.staff.id, event_id=event.id,
            action="labeling.job.reassign.local",
            target_type="labeling_job", target_id=job.id,
            details={"previous": old, "person_id": person.id,
                     "team_id": team.id},
        ))
        db.commit()
        return {"status": "updated", "job_id": job.id}
    native_ready(db)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == "reassign_job",
        NativeOperation.target_id == job.id,
    ))
    if op is not None and op.status in {"pending", "running", "attention"}:
        raise HTTPException(
            status_code=409, detail="Previous reassignment still requires processing.",
        )
    if op is None:
        op = NativeOperation(
            event_id=event.id, kind="reassign_job", target_id=job.id,
            priority=100,
        )
        db.add(op)
    else:
        op.status = "pending"
        op.claimed_at = None
        op.completed_at = None
        op.error_code = None
        op.created_at = epoch()
    op.payload = {"person_id": person.id, "team_id": team.id,
                  "requested_by": ctx.staff.id}
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="labeling.job.reassign.requested",
        target_type="labeling_job", target_id=job.id,
        details={"person_id": person.id, "team_id": team.id},
    ))
    db.commit()
    return {"status": "pending", "job_id": job.id, "operation_id": op.id}
