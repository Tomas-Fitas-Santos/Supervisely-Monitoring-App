"""Deterministic morphology-aware, frame-weighted, video-atomic job planning.

A job is the pair-level unit of progress. It is not a claim that Supervisely
supports two annotators editing the same native answer simultaneously.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .annotation_schema import annotation_class_view
from .auth import AuthContext, requires
from .datasets_api import get_dataset
from .db import get_db
from .events_api import current_event
from .models import (
    AuditEvent,
    CompetitionTeam,
    LabelingJob,
    LabelingPlan,
    MediaAsset,
    MediaVideo,
    MonitorAssignment,
    WorkBlock,
    epoch,
    identifier,
)
from .pair_members import pair_members

router = APIRouter(prefix="/api/v1/labeling", tags=["labeling plans"])
VIEWERS = ("organiser", "monitor", "observer", "activity", "distribution", "reception")


@router.get("/annotation-classes")
def list_annotation_classes(
    _ctx: AuthContext = Depends(requires(*VIEWERS)),
):
    """The exact geometric class and color contract the native worker deploys."""
    return {"items": annotation_class_view(), "project_scope": "Supervisely project"}



class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    mode: Literal["legacy", "shared_30_v1"] = "legacy"
    jobs_per_team: int = Field(default=4, ge=1, le=100)
    redundancy: int = Field(default=1, ge=1, le=1)
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
        videos_for_team = [
            unit for unit in selected[team] if unit.video_group is not None
        ]
        images_for_team = [
            unit for unit in selected[team] if unit.video_group is None
        ]
        # Each job owns one complete video, never a partial video or two videos.
        # Standalone images, when present, go into separate image-only jobs.
        if len(videos_for_team) + (1 if images_for_team else 0) > jobs_per_team:
            raise HTTPException(
                status_code=422,
                detail="Jobs per team is too small for one complete video per job. "
                       "Increase it or lower cross-team redundancy.",
            )
        assigned_jobs: list[JobGroup] = []
        for unit in sorted(videos_for_team, key=lambda u: u.id):
            assigned_jobs.append(JobGroup(
                media_ids=list(unit.media_ids), frame_count=unit.weight,
                video_groups={unit.video_group},
                morph_counts=Counter({unit.morphology: unit.weight}),
            ))
        if images_for_team:
            remaining = jobs_per_team - len(assigned_jobs)
            image_buckets = [JobGroup() for _ in range(remaining)]
            for unit in sorted(images_for_team, key=lambda u: (-u.weight, u.id)):
                bucket = min(image_buckets, key=lambda g: g.frame_count)
                bucket.media_ids.extend(unit.media_ids)
                bucket.frame_count += unit.weight
                bucket.morph_counts[unit.morphology] += unit.weight
            assigned_jobs.extend(b for b in image_buckets if b.media_ids)
        result[team] = assigned_jobs
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


@router.get("/datasets/{dataset_id}/shared-readiness")
def shared_source_readiness(
    dataset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    """Read-only preflight for automatic 30-frame jobs, not upload status.

    Aggregate by source video in SQL so the page can inspect 120k+ frames
    without transferring the full frame catalogue to the browser.
    Final plan creation still enforces the authoritative full validation.
    """
    event = current_event(db, ctx)
    ds = get_dataset(db, event.id, dataset_id)
    scope = (MediaAsset.event_id == event.id,
             MediaAsset.dataset_id == ds.id)
    total = db.scalar(select(func.count(MediaAsset.id)).where(*scope)) or 0
    videos = {
        item.id: item for item in db.scalars(select(MediaVideo).where(
            MediaVideo.event_id == event.id,
            MediaVideo.dataset_id == ds.id,
        ))
    }
    issues: list[str] = []

    if ds.status != "ready":
        issues.append("Local dataset import/indexing is not complete.")
    if total == 0:
        issues.append("Dataset has no imported frames.")
    unlinked = db.scalar(select(MediaAsset.relative_path).where(
        *scope, MediaAsset.video_id.is_(None),
    ).order_by(MediaAsset.relative_path).limit(1))
    if unlinked:
        issues.append(
            f"Frame '{unlinked}' has no source-video identity. "
            "Use species/video.mp4 or species/video-name/frame_000001.jpg folders."
        )
    missing_ordinal = db.scalar(select(MediaAsset.relative_path).where(
        *scope, MediaAsset.frame_index.is_(None),
    ).order_by(MediaAsset.relative_path).limit(1))
    if missing_ordinal:
        issues.append(
            f"Frame '{missing_ordinal}' has no source-frame position. "
            "Imported frames must have a sortable position in their source video."
        )
    groups = db.execute(select(
        MediaAsset.video_id, func.count(MediaAsset.id),
        func.count(func.distinct(MediaAsset.frame_index)),
    ).where(*scope, MediaAsset.video_id.is_not(None))
      .group_by(MediaAsset.video_id)).all()
    # Sampled videos are supported: gaps in original frame numbers are
    # intentional. Verify the *imported* frames have distinct, ordered
    # positions; never insist on every original video frame being present.
    for video_id, count, unique_positions in groups:
        video = videos.get(video_id)
        if video is None:
            issues.append(f"Frame group '{video_id}' refers to an unknown source video.")
            continue
        if count != video.frame_count:
            issues.append(
                f"Video '{video.video_group}' has {count} imported frames, "
                f"but its dataset record declares {video.frame_count}."
            )
        if unique_positions != count:
            issues.append(
                f"Video '{video.video_group}' has missing or duplicate imported "
                "frame positions."
            )
    from .native_media_pool import mirror_status
    mirror = mirror_status(db, ds) if ds.status == "ready" else None
    source_ready = not issues and bool(groups)
    return {
        "dataset_id": ds.id,
        "source_ready": source_ready,
        "mirror_ready": bool(mirror and mirror["ready_for_automatic_jobs"]),
        "ready_for_distribution": (
            source_ready and bool(mirror and mirror["ready_for_automatic_jobs"])
        ),
        "source_frames": total,
        "source_videos": len(videos),
        "primary_jobs": (total + 29) // 30,
        "issues": issues[:8],
        "extra_issues": max(0, len(issues) - 8),
    }


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
    shared_pool = db.scalar(select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id, LabelingPlan.dataset_id == dataset.id,
        LabelingPlan.queue_seed.is_not(None),
        LabelingPlan.status.in_(["planned", "approved", "running", "paused"]),
    ).limit(1))
    if shared_pool:
        raise HTTPException(status_code=409, detail="This dataset is already reserved by a shared queue plan.")
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
    for team in teams:
        people, group = pair_members(db, event.id, team.id)
        if not people or group == "invalid":
            raise HTTPException(
                status_code=422,
                detail="Every team needs one or two active participants or two active "
                       "monitor-pair members before planning.",
            )
    assets = list(db.scalars(select(MediaAsset).where(
        MediaAsset.event_id == event.id, MediaAsset.dataset_id == dataset.id,
    ).order_by(MediaAsset.video_group, MediaAsset.frame_index.nulls_last(), MediaAsset.relative_path, MediaAsset.id)))
    if not assets:
        raise HTTPException(status_code=422, detail="Dataset has no media assets.")
    # The automatic pool is exclusive across ALL teams; manual video reuse
    # is the only explicit way to assign duplicate video footage.
    prior_jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event.id,
        LabelingJob.dataset_id == dataset.id,
    )))
    reserved = {mid for job in prior_jobs for mid in job.media_ids}
    if payload.mode == "shared_30_v1":
        if reserved or payload.video_limit is not None:
            raise HTTPException(status_code=409, detail="Use an unassigned dataset without a video limit for the shared queue.")
        videos = {video.id: video for video in db.scalars(select(MediaVideo).where(
            MediaVideo.event_id == event.id, MediaVideo.dataset_id == dataset.id,
        ))}
        by_video: dict[str, list[MediaAsset]] = defaultdict(list)
        for asset in assets:
            video = videos.get(asset.video_id)
            if not video:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Frame '{asset.relative_path}' is not linked to a source video. "
                        "For automatic 30-frame jobs, import dataset/species/video.mp4 "
                        "or dataset/species/video-name/frame_000001.jpg. "
                        "Source frames may be intentionally sampled, but each "
                        "imported frame must belong to a video."
                    ),
                )
            if asset.frame_index is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Frame '{asset.relative_path}' in video "
                        f"'{video.video_group}' has no source-frame number. "
                        "Imported frames need a sortable position; sampled "
                        "frame numbers do not need to be consecutive."
                    ),
                )
            by_video[video.id].append(asset)

        # The ordered stream uses only the frames IN THE UPLOADED DATASET.
        # Sampled frames may have any ascending original frame numbers.
        # Never invent or require the omitted source-video frames.
        # Only the final dataset-wide block may contain fewer than 30 frames.
        stream: list[tuple[str, str]] = []
        for video_id in sorted(by_video, key=lambda vid: (videos[vid].video_group, vid)):
            source_frames = sorted(by_video[video_id], key=lambda asset: asset.frame_index)
            if len(source_frames) != videos[video_id].frame_count:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Video '{videos[video_id].video_group}' contains "
                        f"{len(source_frames)} imported frames, but its dataset "
                        f"record declares {videos[video_id].frame_count}."
                    ),
                )
            if any(source_frames[index].frame_index == source_frames[index - 1].frame_index
                   for index in range(1, len(source_frames))):
                raise HTTPException(
                    status_code=422,
                    detail=f"Video '{videos[video_id].video_group}' has duplicate "
                           "imported frame positions.",
                )
            stream.extend((video_id, asset.id) for asset in source_frames)

        plan = LabelingPlan(event_id=event.id, dataset_id=dataset.id, jobs_per_team=0,
                            redundancy=1, created_by=ctx.staff.id, queue_seed=identifier())
        db.add(plan)
        db.flush()
        blocks = []
        mixed_video_blocks = 0
        for offset in range(0, len(stream), 30):
            chunk = stream[offset:offset + 30]
            media_ids = [media_id for _video_id, media_id in chunk]
            mixed_video_blocks += len({video_id for video_id, _mid in chunk}) > 1
            blocks.append(WorkBlock(
                event_id=event.id, plan_id=plan.id,
                # Anchor for the existing FK; media_ids are authoritative for
                # all video identities within a mixed-video job.
                video_id=chunk[0][0], ordinal=offset // 30 + 1,
                frame_count=len(media_ids), media_ids=media_ids,
                repeat_media_ids=list(media_ids) if len(media_ids) == 30 else [],
            ))
        db.add_all(blocks)
        summary = {"primary_blocks": len(blocks), "unique_frames": len(stream),
                   "mixed_video_blocks": mixed_video_blocks,
                   "short_tails": sum(block.frame_count < 30 for block in blocks),
                   "short_final_frames": blocks[-1].frame_count if blocks[-1].frame_count < 30 else 0,
                   "repeat_frames": sum(len(block.repeat_media_ids) for block in blocks)}
        db.add(AuditEvent(staff_id=ctx.staff.id, event_id=event.id, action="labeling.plan.create",
                          target_type="labeling_plan", target_id=plan.id,
                          details={"mode": "shared_30_v1", **summary}))
        db.commit()
        return {"plan_id": plan.id, "status": plan.status, "mode": payload.mode, **summary}
    assets = [item for item in assets if item.id not in reserved]
    whole_video_groups = {asset.video_group for asset in assets if asset.video_group}
    if assets and all(item.video_group for item in assets):
        if payload.jobs_per_team < 2:
            raise HTTPException(
                status_code=422,
                detail="At least two jobs per team are required for the two annotators.",
            )
        if len(whole_video_groups) < 2 * len(teams):
            raise HTTPException(
                status_code=422,
                detail="There must be at least two different unassigned videos "
                       "per team for automatic competition.",
            )
    groups, totals = allocate(
        build_units(assets), [t.id for t in teams], payload.jobs_per_team,
        payload.redundancy, payload.video_limit,
    )
    if (assets and all(item.video_group for item in assets)
            and any(totals["video_counts"].get(t.id, 0) < 2 for t in teams)):
            raise HTTPException(
                status_code=422,
                detail="Automatic allocation must give both members of every team "
                       "different videos. Add more videos or adjust the team count.",
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
        members, _ = pair_members(db, event.id, team.id)
        members = sorted(members, key=lambda p: p.id)
        for seq, group in enumerate(groups[team.id], start=1):
            db.add(LabelingJob(
                event_id=event.id, dataset_id=dataset.id, plan_id=plan.id,
                team_id=team.id, sequence=seq,
                assignee_person_id=members[(seq - 1) % len(members)].id,
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
        "mode": "shared_30_v1" if p.queue_seed else "legacy",
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
    conflicts = select(LabelingPlan.id).where(
        LabelingPlan.event_id == event.id,
        LabelingPlan.status.in_(["approved", "running", "paused"]),
    )
    if plan.queue_seed:
        conflicts = conflicts.where(or_(LabelingPlan.jobs_per_team > 0, LabelingPlan.queue_seed.is_not(None)))
    else:
        conflicts = conflicts.where(or_(LabelingPlan.status == "approved", LabelingPlan.queue_seed.is_not(None)))
    active = db.scalar(conflicts.limit(1))
    if active:
        raise HTTPException(status_code=409, detail="Another automatic plan is already approved or active.")
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
    cursor: str | None = None,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    from .job_reads import read_job_page
    event = current_event(db, ctx)
    return read_job_page(db, ctx, event.id, plan_id=plan_id, team_id=team_id,
                         status=status, limit=limit, cursor=cursor)


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
            "frame_index": assets[mid].frame_index,
            "presentation_timestamp": assets[mid].presentation_timestamp,
            "frame_width": assets[mid].frame_width, "frame_height": assets[mid].frame_height,
            "native_image_id": assets[mid].native_image_id,
        } for mid in ids if mid in assets],
    }
