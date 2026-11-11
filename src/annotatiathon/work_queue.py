"""Primary block construction and transactional shared queue claims."""
import hashlib
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    CompetitionTeam,
    Event,
    LabelingJob,
    LabelingPlan,
    MediaVideo,
    Person,
    WorkBlock,
    WorkPacket,
)
from .pair_members import pair_members


def split_video(ordered_asset_ids: list[str], block_size: int = 30) -> list[list[str]]:
    """Split one video's ordered canonical IDs without crossing video boundaries."""
    if block_size < 1:
        raise ValueError("Block size must be positive")
    if len(set(ordered_asset_ids)) != len(ordered_asset_ids):
        raise ValueError("A source frame must occur once in its video's primary coverage")
    return [ordered_asset_ids[start:start + block_size]
            for start in range(0, len(ordered_asset_ids), block_size)]


def sample_repeat_frames(block: list[str], *, seed: str, block_key: str) -> list[str]:
    """Draw at most two distinct canonical frames, reproducibly across retries."""
    ranked = sorted(block, key=lambda asset_id: (
        hashlib.sha256(f"{seed}\0{block_key}\0{asset_id}".encode()).digest(), asset_id,
    ))
    if len(set(block)) != len(block):
        raise ValueError("Repeat candidates must have distinct canonical IDs")
    return ranked[:2]


def reserve_repeats(db: Session, plan: LabelingPlan, eligible_team_ids: list[str]) -> None:
    """Assign both samples of each claimed block to one independent team."""
    if not eligible_team_ids:
        return
    rows = db.execute(select(WorkBlock, LabelingJob.team_id).join(
        LabelingJob, LabelingJob.id == WorkBlock.job_id,
    ).where(
        WorkBlock.plan_id == plan.id, WorkBlock.repeat_team_id.is_(None),
        LabelingJob.team_id.not_in(eligible_team_ids) if len(eligible_team_ids) == 1 else True,
    ).order_by(WorkBlock.id).limit(1000).with_for_update(skip_locked=True, of=WorkBlock))
    for block, primary_team_id in rows:
        candidates = [team_id for team_id in eligible_team_ids if team_id != primary_team_id]
        if not candidates or not block.repeat_media_ids:
            continue
        block.repeat_team_id = min(
            candidates,
            key=lambda team_id: hashlib.sha256(
                f"{plan.queue_seed}:{block.id}:{team_id}".encode(),
            ).digest(),
        )


def repeat_batch(db: Session, plan_id: str, team_id: str) -> tuple[list[WorkBlock], int]:
    blocks = db.scalars(select(WorkBlock).where(
        WorkBlock.plan_id == plan_id, WorkBlock.repeat_team_id == team_id,
        WorkBlock.repeat_job_id.is_(None),
    ).order_by(WorkBlock.id).limit(30).with_for_update(skip_locked=True, of=WorkBlock))
    selected = []
    count = 0
    for block in blocks:
        size = len(block.repeat_media_ids)
        if not size or count + size > 30:
            continue
        selected.append(block)
        count += size
        if count == 30:
            break
    return selected, count


def claim_primary_block(
    db: Session, plan_id: str, team_id: str, person_id: str, *, include_repeats: bool = False,
) -> LabelingJob | None:
    """Claim a primary block or repeat batch and enqueue its native publication in the caller's transaction."""
    from .native_operations_api import native_identity_assignable, queue_publish

    plan = db.scalar(select(LabelingPlan).join(Event, Event.id == LabelingPlan.event_id).where(
        LabelingPlan.id == plan_id, LabelingPlan.queue_seed.is_not(None),
        LabelingPlan.status == "running", Event.status == "active",
    ).with_for_update(read=True, of=(Event, LabelingPlan)))
    if plan is None:
        return None
    team = db.scalar(select(CompetitionTeam).where(
        CompetitionTeam.id == team_id, CompetitionTeam.event_id == plan.event_id,
        CompetitionTeam.active.is_(True),
    ).with_for_update())
    if team is None:
        return None
    person = db.scalar(select(Person).where(
        Person.id == person_id, Person.event_id == plan.event_id, Person.active.is_(True),
    ).with_for_update().execution_options(populate_existing=True))
    members, _ = pair_members(db, plan.event_id, team.id)
    if person not in members or not native_identity_assignable(person):
        return None
    if db.scalar(select(WorkPacket.job_id).where(
        WorkPacket.event_id == plan.event_id, WorkPacket.person_id == person.id,
        WorkPacket.submitted_at.is_(None),
    )) is not None:
        return None
    repeats, repeat_count = repeat_batch(db, plan.id, team.id) if include_repeats else ([], 0)
    block = None
    if repeat_count < 30:
        assigned = func.count(WorkBlock.job_id)
        progress = select(
            WorkBlock.video_id,
            (assigned > 0).label("started"),
            (func.count() - assigned).label("remaining"),
        ).where(WorkBlock.plan_id == plan.id).group_by(WorkBlock.video_id).subquery()
        block = db.scalar(select(WorkBlock).join(progress, progress.c.video_id == WorkBlock.video_id).where(
            WorkBlock.plan_id == plan.id, WorkBlock.event_id == plan.event_id, WorkBlock.job_id.is_(None),
        ).order_by(progress.c.started.desc(), progress.c.remaining, WorkBlock.video_id, WorkBlock.ordinal)
            .limit(1).with_for_update(skip_locked=True, of=WorkBlock))
    if block is None and not repeats:
        return None
    selected = [block] if block is not None else repeats
    is_repeat = block is None
    frame_ids = []
    morphologies = Counter()
    for source in selected:
        ids = source.repeat_media_ids if is_repeat else source.media_ids
        frame_ids.extend(ids)
        video = db.get(MediaVideo, source.video_id)
        morphologies[video.video_group.split("/", 1)[0]] += len(ids)
    sequence = db.scalar(select(func.max(LabelingJob.sequence)).where(
        LabelingJob.plan_id == plan.id, LabelingJob.team_id == team.id,
    )) or 0
    job = LabelingJob(
        event_id=plan.event_id, plan_id=plan.id, dataset_id=plan.dataset_id, team_id=team.id,
        assignee_person_id=person.id, release_mode="shared_repeat" if is_repeat else "shared_primary",
        sequence=sequence + 1, media_ids=frame_ids, frame_count=len(frame_ids),
        video_count=len({source.video_id for source in selected}), morphology_counts=dict(morphologies),
    )
    db.add(job)
    db.flush()
    for source in selected:
        if is_repeat:
            source.repeat_job_id = job.id
        else:
            source.job_id = job.id
    db.add(WorkPacket(job_id=job.id, event_id=plan.event_id, person_id=person.id))
    queue_publish(db, job)
    return job
