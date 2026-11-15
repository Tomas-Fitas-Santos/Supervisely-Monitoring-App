"""Explicit maintenance for checked-out shared-video claims; no service restart.

Stop/archive native attempts without deleting annotations, capture complete frames
for their original team, then atomically repack only unresolved source frames.
The existing scheduler consumes ordinary WorkBlocks without new worker code.
Run with an exact event ID and job-number allowlist. Default is read-only.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from annotatiathon.db import session_factory
from annotatiathon.frame_submission import accepted_ids, capture_frame_submission, pending_ids
from annotatiathon.models import (
    AuditEvent, Event, LabelingJob, LabelingPlan, MediaAsset, MediaDataset,
    NativeJobBinding, NativeMediaInstance, NativeOperation, NativeSourceMedia,
    Person, ReviewSample, SuperviselyConfig,
    WorkBlock, WorkPacket, epoch,
)

KIND = "checkout_requeue"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def job_scope(db, event_id, number):
    job = db.scalar(select(LabelingJob).where(
        LabelingJob.event_id == event_id, LabelingJob.native_job_number == number,
    ).with_for_update().execution_options(populate_existing=True))
    require(job is not None, "job_not_found")
    return job


def check_job(db, job):
    require(db.get(Event, job.event_id).status == "active", "event_not_active")
    plan = db.get(LabelingPlan, job.plan_id)
    require(plan.allocation_mode == "shared_30_v1" and plan.status == "running", "unsupported_plan")
    require(job.release_mode == "shared_primary" and job.assignee_person_id, "not_primary_claim")
    # Lock people to fence simultaneous check-in; no team-wide attendance guess.
    members = list(db.scalars(select(Person).where(
        Person.event_id == job.event_id, Person.team_id == job.team_id,
        Person.kind == "participant", Person.active.is_(True),
    ).order_by(Person.id).with_for_update().execution_options(populate_existing=True)))
    require(members and all(p.check_in_at is None for p in members), "team_has_present_members")
    require(any(p.id == job.assignee_person_id for p in members), "assignee_team_changed")
    require(job.status == "in_progress", "job_no_longer_in_progress")
    require(not db.scalar(select(NativeOperation.id).where(
        NativeOperation.target_id == job.id, NativeOperation.kind != KIND,
        NativeOperation.status.in_(["pending", "running", "attention"]),
    ).limit(1)), "job_has_active_operations")
    require(not db.scalar(select(ReviewSample.id).where(
        ReviewSample.job_id == job.id, ReviewSample.validated_complete.is_not(True),
    ).limit(1)), "legacy_reviews_need_manual_reconciliation")
    blocks = list(db.scalars(select(WorkBlock).where(WorkBlock.job_id == job.id)))
    require(len(blocks) == 1 and blocks[0].media_ids == job.media_ids, "source_block_changed")
    require(blocks[0].repeat_team_id is None and blocks[0].repeat_job_id is None, "block_has_repeat_claim")
    bindings = list(db.scalars(select(NativeJobBinding).where(NativeJobBinding.job_id == job.id)))
    require(len(bindings) == 1 and bindings[0].person_id == job.assignee_person_id, "binding_changed")
    packet = db.get(WorkPacket, job.id)
    require(packet is not None and packet.person_id == job.assignee_person_id
            and packet.submitted_at is None, "open_packet_changed")
    return blocks[0], bindings[0], packet


def prepare_job(db, api, event_id, number):
    """Freeze one native attempt and retain all saved JSON before queue changes."""
    job = job_scope(db, event_id, number)
    op = db.scalar(select(NativeOperation).where(
        NativeOperation.kind == KIND, NativeOperation.target_id == job.id,
    ))
    if op is not None:
        require(op.status in {"pending", "done"}, "previous_return_needs_reconciliation")
        db.rollback()
        return {"job": number, "status": op.status, "already_prepared": True}
    block, binding, packet = check_job(db, job)
    payload = {"job_number": number, "native_job_id": binding.native_job_id,
               "previous_status": job.status, "previous_review_status": job.review_status,
               "team_id": job.team_id, "person_id": job.assignee_person_id,
               "block_id": block.id, "media_ids": list(job.media_ids)}
    op = NativeOperation(event_id=event_id, kind=KIND, target_id=job.id,
                         status="running", payload=payload, claimed_at=epoch())
    db.add(op)
    db.commit()  # Durable intent precedes remote writes.
    jid, oid = job.id, op.id
    remote_changed = False
    try:
        job = job_scope(db, event_id, number)
        block, binding, packet = check_job(db, job)
        native = api.labeling_job.get_info_by_id(binding.native_job_id)
        person = db.get(Person, job.assignee_person_id)
        require(native is not None and native.dataset_id == job.native_dataset_id
                and native.assigned_to_id == person.supervisely_user_id, "native_identity_changed")
        require(native.status in {"pending", "in_progress"} and not native.disabled,
                "native_job_already_submitted_or_disabled")
        remote_changed = True  # A failed response may still have stopped it.
        api.labeling_job.stop(native.id)
        stopped = api.labeling_job.get_info_by_id(native.id)
        require(stopped.status == "stopped", "native_stop_not_confirmed")
        unresolved = pending_ids(db, job)
        mapping = {m.media_id: m.native_image_id for m in db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.job_id == job.id, NativeMediaInstance.event_id == event_id,
        ))}
        require(set(mapping) == set(job.media_ids), "native_media_mapping_changed")
        infos = api.image.get_list(job.native_dataset_id, filters=[
            {"field": "id", "operator": "in", "value": [mapping[mid] for mid in unresolved]},
        ]) if unresolved else []
        by_id = {i.id: i for i in infos}
        require(set(by_id) == {mapping[mid] for mid in unresolved}, "native_images_changed")
        anns = api.labeling_job.get_annotations(native.id,
            image_infos=[by_id[mapping[mid]] for mid in unresolved],
            project_meta=api.labeling_job.get_project_meta(native.id)) if unresolved else []
        require(len(anns) == len(unresolved), "annotation_backup_count_mismatch")
        dataset = db.get(MediaDataset, job.dataset_id)
        backup = Path(dataset.root_path) / "annotations" / job.id / "checkout-requeue-backup.json"
        require(not backup.exists(), "annotation_backup_already_exists")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("x", encoding="utf-8") as stream:
            json.dump({**payload, "captured_at": epoch(), "annotations": [
                {"source_media_id": mid, "native_image_id": mapping[mid], "annotation": ann.to_json()}
                for mid, ann in zip(unresolved, anns)
            ]}, stream, ensure_ascii=False)
        # The normal structural validator credits complete saved frames to their
        # original team. Incomplete JSON is retained above, never fabricated.
        capture_frame_submission(db, api, job)
        remaining = pending_ids(db, job)
        api.labeling_job.archive(native.id)
        archived = api.labeling_job.get_info_by_id(native.id)
        require(archived is not None and archived.disabled and archived.status == "stopped",
                "native_archive_not_confirmed")
        retained = accepted_ids(db, job)
        retained_assets = list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(retained))))
        # Finalize only the portion actually delivered by the original team.
        # Leaving the old 30-frame count would make the validation scheduler
        # reopen its unresolved subset after those frames have been requeued.
        job.media_ids = [mid for mid in job.media_ids if mid in retained]
        job.frame_count = len(job.media_ids)
        job.video_count = len({a.video_id for a in retained_assets})
        job.morphology_counts = dict(Counter(a.morphology for a in retained_assets))
        job.status = "completed"
        job.review_status = "requeued_checkout"
        job.completed_at = epoch()
        packet.submitted_at = packet.submitted_at or epoch()
        binding.native_status = archived.status
        binding.last_synced_at = epoch()
        op = db.get(NativeOperation, oid)
        op.status = "pending"  # Private maintenance pool; existing workers ignore this kind.
        op.payload = {**payload, "pending_media_ids": remaining,
                      "accepted_media_ids": sorted(retained), "backup": str(backup)}
        db.add(AuditEvent(event_id=event_id, action="native.job.checkout_return.prepared",
            target_type="labeling_job", target_id=jid, details=op.payload))
        db.commit()
        return {"job": number, "status": "prepared", "returned_frames": len(remaining),
                "retained_complete_frames": len(retained)}
    except Exception:
        db.rollback()
        op = db.get(NativeOperation, oid)
        op.status = "attention"
        op.error_code = "checkout_return_reconcile_required"
        if remote_changed:
            parked = db.get(LabelingJob, jid)
            parked.status, parked.review_status = "needs_correction", "checkout_return_attention"
        db.commit()
        raise


def release_returned_media_references(db, ops):
    """Release unique live image ownership; archived attempts retain JSON/IDs in the journal."""
    removed = 0
    for op in ops:
        require(op.status in {"pending", "done"}, "return_pool_not_ready")
        job = db.scalar(select(LabelingJob).where(LabelingJob.id == op.target_id)
            .with_for_update().execution_options(populate_existing=True))
        require(job.status == "completed" and job.review_status == "requeued_checkout", "prepared_job_changed")
        pending = set(op.payload["pending_media_ids"])
        require(pending.isdisjoint(job.media_ids), "returned_reference_still_owned")
        mappings = list(db.scalars(select(NativeMediaInstance).where(
            NativeMediaInstance.job_id == job.id, NativeMediaInstance.media_id.in_(pending),
        ).with_for_update()))
        source = {m.media_id: m for m in db.scalars(select(NativeSourceMedia).where(
            NativeSourceMedia.dataset_id == job.dataset_id, NativeSourceMedia.media_id.in_(pending),
        ))}
        for mapping in mappings:
            canonical = source.get(mapping.media_id)
            require(canonical is not None and mapping.native_image_id == canonical.native_image_id
                    and mapping.native_dataset_id == canonical.native_dataset_id,
                    "returned_native_reference_changed")
            db.delete(mapping)
        removed += len(mappings)
        if mappings:
            db.add(AuditEvent(event_id=job.event_id, action="native.job.checkout_references.released",
                target_type="labeling_job", target_id=job.id,
                details={"media_ids": [m.media_id for m in mappings], "backup": op.payload["backup"]}))
    db.flush()
    return removed


def finish_returns(db, event_id, numbers):
    """One short SQL transaction preserves source coverage and excludes accepted frames."""
    jobs = list(db.scalars(select(LabelingJob).where(
        LabelingJob.event_id == event_id, LabelingJob.native_job_number.in_(numbers),
    )))
    require(len(jobs) == len(numbers) and len({j.plan_id for j in jobs}) == 1, "job_allowlist_changed")
    plan = db.scalar(select(LabelingPlan).where(LabelingPlan.id == jobs[0].plan_id)
        .with_for_update().execution_options(populate_existing=True))
    require(plan.status == "running" and plan.allocation_mode == "shared_30_v1", "plan_changed")
    ops = list(db.scalars(select(NativeOperation).where(
        NativeOperation.kind == KIND, NativeOperation.target_id.in_([j.id for j in jobs]),
    ).with_for_update()))
    require(len(ops) == len(jobs), "some_jobs_not_prepared")
    if all(op.status == "done" for op in ops):
        released = release_returned_media_references(db, ops)
        db.commit()
        return {"status": "already_done", **({"released_obsolete_references": released} if released else {})}
    require(all(op.status == "pending" for op in ops), "return_pool_not_ready")
    blocks = list(db.scalars(select(WorkBlock).where(WorkBlock.plan_id == plan.id)
                            .with_for_update().execution_options(populate_existing=True)))
    before = [mid for block in blocks for mid in block.media_ids]
    dataset = db.get(MediaDataset, plan.dataset_id)
    source_ids = set(db.scalars(select(MediaAsset.id).where(MediaAsset.dataset_id == dataset.id)))
    require(len(before) == len(set(before)) and set(before) == source_ids, "source_coverage_changed")
    by_job = {block.job_id: block for block in blocks if block.job_id}
    available = [block for block in blocks if block.job_id is None]
    pool = [mid for block in available for mid in block.media_ids]
    prior_blocks = [{"id": b.id, "video_id": b.video_id, "ordinal": b.ordinal,
                     "media_ids": b.media_ids} for b in available]
    for op in ops:
        job = db.get(LabelingJob, op.target_id, populate_existing=True)
        require(job.status == "completed" and job.review_status == "requeued_checkout", "prepared_job_changed")
        block = by_job[job.id]
        returned = op.payload["pending_media_ids"]
        retained = accepted_ids(db, job)
        require(set(returned).isdisjoint(retained)
                and set(returned) | retained == set(block.media_ids), "return_frame_partition_changed")
        pool.extend(returned)
        if retained:
            block.media_ids = [mid for mid in block.media_ids if mid in retained]
            block.frame_count = len(block.media_ids)
            block.repeat_media_ids = []
        else:
            db.delete(block)
    require(len(pool) == len(set(pool)), "duplicate_returned_source_frames")
    require(not any(b.repeat_team_id or b.repeat_job_id for b in available), "available_block_has_repeat_claim")
    for block in available:
        db.delete(block)
    db.flush()
    assets = list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(pool)).order_by(
        MediaAsset.video_group, MediaAsset.frame_index, MediaAsset.id,
    )))
    require(len(assets) == len(pool), "return_source_assets_missing")
    next_ordinal = (db.scalar(select(func.max(WorkBlock.ordinal)).where(WorkBlock.plan_id == plan.id)) or 0) + 1
    new_blocks = []
    for start in range(0, len(assets), 30):
        chunk = assets[start:start + 30]
        block = WorkBlock(event_id=event_id, plan_id=plan.id, video_id=chunk[0].video_id,
                          ordinal=next_ordinal + start // 30, frame_count=len(chunk),
                          media_ids=[a.id for a in chunk], repeat_media_ids=[])
        db.add(block)
        new_blocks.append(block)
    db.flush()
    after = [mid for ids in db.scalars(select(WorkBlock.media_ids).where(WorkBlock.plan_id == plan.id)) for mid in ids]
    require(len(after) == len(before) and len(set(after)) == len(after)
            and set(after) == source_ids, "source_coverage_not_preserved")
    released = release_returned_media_references(db, ops)
    for op in ops:
        op.status, op.completed_at = "done", epoch()
        db.add(AuditEvent(event_id=event_id, action="native.job.checkout_requeued",
                         target_type="labeling_job", target_id=op.target_id, details=op.payload))
    summary = {"jobs": len(ops), "returned_frames": sum(len(op.payload["pending_media_ids"]) for op in ops),
               "retained_complete_frames": sum(len(op.payload["accepted_media_ids"]) for op in ops),
               "available_frames": len(pool), "queue_blocks": len(new_blocks),
               "source_frames": len(source_ids), "coverage_preserved": True,
               "released_obsolete_references": released}
    db.add(AuditEvent(event_id=event_id, action="labeling.queue.checkout_repack",
        target_type="labeling_plan", target_id=plan.id,
        details={**summary, "prior_unassigned_blocks": prior_blocks,
                 "new_block_ids": [b.id for b in new_blocks]}))
    db.commit()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--jobs", required=True, type=int, nargs="+")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(len(set(args.jobs)) == len(args.jobs), "duplicate_job_numbers")
    with session_factory()() as db:
        if not args.apply:
            for number in args.jobs:
                job = job_scope(db, args.event, number)
                check_job(db, job)
            db.rollback()
            print(json.dumps({"eligible_jobs": len(args.jobs), "mutations": False}), flush=True)
            return
        from annotatiathon.native_worker import connect_native
        api = connect_native(db.get(SuperviselyConfig, 1))
        for number in args.jobs:
            print(json.dumps(prepare_job(db, api, args.event, number)), flush=True)
        print(json.dumps(finish_returns(db, args.event, args.jobs)), flush=True)


if __name__ == "__main__":
    main()
