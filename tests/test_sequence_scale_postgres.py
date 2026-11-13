"""50k-frame handoff boundary with separate datasets; no real media or remote API."""
import json
import os
from time import perf_counter
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.labeling_api import PlanInput, create_plan
from annotatiathon.plan_sequence import activate_waiting_plans
from annotatiathon.work_queue import claim_primary_block


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_50447_video_frames_assigned_before_separate_mounted_image_plan():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    suffix = m.identifier()[:8]
    event, staff, team, person, video_ds, image_ds = [f"{s}-{suffix}" for s in (
        "scale-event", "scale-staff", "scale-team", "scale-person", "scale-videos", "scale-images")]
    total, video_count = 50_447, 149
    photos = [f"scale-photo-{suffix}-{i}" for i in range(33)]
    other_team = f"scale-team2-{suffix}"
    next_person = f"scale-person2-{suffix}"
    image_person = f"scale-person3-{suffix}"
    ctx = SimpleNamespace(staff=SimpleNamespace(id=staff), session=SimpleNamespace(event_id=event))
    started = perf_counter()
    with engine.begin() as conn:
        conn.execute(m.Event.__table__.insert(), {"id": event, "name": f"Scale rehearsal {suffix}"})
        conn.execute(m.StaffAccount.__table__.insert(), {"id": staff,
            "email": f"{staff}@example.test", "name": "Synthetic organiser",
            "password_hash": "disabled", "roles": ["organiser"]})
        conn.execute(m.CompetitionTeam.__table__.insert(), {"id": team, "event_id": event, "name": "Synthetic team"})
        conn.execute(m.Person.__table__.insert(), {"id": person, "event_id": event, "team_id": team,
            "name": "Synthetic annotator", "email": f"{person}@example.test", "kind": "participant",
            "membership_status": "joined", "supervisely_user_id": 1, "supervisely_role": "annotator",
            "native_verified_at": 1, "check_in_at": 1})
        conn.execute(m.CompetitionTeam.__table__.insert(), {"id": other_team, "event_id": event, "name": "Second team"})
        for i, (pid, tid) in enumerate(((next_person, team), (image_person, other_team))):
            conn.execute(m.Person.__table__.insert(), {"id": pid, "event_id": event, "team_id": tid,
                "name": pid, "email": f"{pid}@example.test", "kind": "participant",
                "membership_status": "joined", "supervisely_user_id": i+2, "supervisely_role": "annotator",
                "native_verified_at": 1, "check_in_at": 1})
        for dsid, kind, frames, images in ((video_ds, "zip", total, 0), (image_ds, "mounted", 0, 33)):
            conn.execute(m.MediaDataset.__table__.insert(), {"id": dsid, "event_id": event,
                "name": dsid, "upload_kind": kind, "root_path": "/test/no-real-media",
                "source_folder": "standalone-images" if images else None,
                "created_by": staff, "status": "ready", "frame_count": frames, "image_count": images,
                "video_count": video_count if frames else 0, "native_dataset_id": 42 if images else 41,
                "inventory_revision": 1, "mirrored_revision": 1})
        video_rows, asset_rows = [], []
        for i in range(video_count):
            count = total // video_count + int(i < total % video_count)
            vid, group = f"scale-clip-{suffix}-{i}", f"unclassified/clip-{i:03d}"
            video_rows.append({"id": vid, "event_id": event, "dataset_id": video_ds,
                "video_group": group, "extraction_policy": "all_frames", "frame_count": count,
                "density": "declared_full", "identity_status": "complete"})
            for ordinal in range(count):
                asset_rows.append({"id": f"scale-frame-{suffix}-{i}-{ordinal}", "event_id": event,
                    "dataset_id": video_ds, "video_id": vid, "video_group": group,
                    "frame_index": ordinal, "relative_path": f"videos/{group}/{ordinal:06d}.jpg",
                    "kind": "video_frame", "morphology": "unclassified", "size_bytes": 1, "sha256": "0"*64})
        conn.execute(m.MediaVideo.__table__.insert(), video_rows)
        for offset in range(0, len(asset_rows), 1000):
            conn.execute(m.MediaAsset.__table__.insert(), asset_rows[offset:offset+1000])
        conn.execute(m.MediaAsset.__table__.insert(), [{"id": mid, "event_id": event,
            "dataset_id": image_ds, "relative_path": f"images/unclassified/{mid}.jpg", "kind": "image",
            "morphology": "unclassified", "size_bytes": 1, "sha256": "0"*64} for mid in photos])
        conn.execute(m.NativeSourceMedia.__table__.insert(), [{"event_id": event, "dataset_id": image_ds,
            "media_id": mid, "native_dataset_id": 42,
            "native_image_id": 100000000 + int(suffix[:5],16)*33+i} for i, mid in enumerate(photos)])
        conn.execute(m.NativeOperation.__table__.insert(), {"event_id": event,
            "kind": "mirror_dataset", "target_id": image_ds, "status": "done"})

    # Use the production builders, keeping the first plan's blocks intact.
    with Session(engine) as db:
        first = create_plan(PlanInput(dataset_id=video_ds, mode="shared_30_v1"), ctx, db)
        parent_id = first["plan_id"]
        assert (first["primary_blocks"], first["short_final_frames"]) == (1682, 17)
        with pytest.raises(HTTPException, match="different dataset"):
            create_plan(PlanInput(dataset_id=video_ds, mode="images_30_v1", after_plan_id=parent_id), ctx, db)
        db.rollback()
        second = create_plan(PlanInput(dataset_id=image_ds, mode="images_30_v1", after_plan_id=parent_id), ctx, db)
        child_id = second["plan_id"]
        assert child_id != parent_id and second["primary_blocks"] == 2
        db.get(m.LabelingPlan, parent_id).status = "running"
        db.get(m.LabelingPlan, child_id).status = "waiting"
        blocks = list(db.scalars(select(m.WorkBlock).where(
            m.WorkBlock.plan_id == parent_id).order_by(m.WorkBlock.ordinal)))
        original_blocks = [(b.id, list(b.media_ids)) for b in blocks]
        jobs, packets = [], []
        for i, block in enumerate(blocks[:-1]):
            job_id = f"scale-job-{suffix}-{i}"
            jobs.append({"id": job_id, "event_id": event, "dataset_id": video_ds, "plan_id": parent_id,
                "team_id": team, "sequence": i+1, "assignee_person_id": person, "release_mode": "shared_primary",
                "status": "in_progress" if i == len(blocks)-2 else "on_review",
                "submitted_at": None if i == len(blocks)-2 else 1, "media_ids": block.media_ids,
                "frame_count": block.frame_count, "video_count": 1, "morphology_counts": {"unclassified": block.frame_count}})
            packets.append({"job_id": job_id, "event_id": event, "person_id": person,
                "submitted_at": None if i == len(blocks)-2 else 1})
        db.execute(m.LabelingJob.__table__.insert(), jobs)
        db.execute(m.WorkPacket.__table__.insert(), packets)
        for block, job in zip(blocks, jobs):
            block.job_id = job["id"]
        db.commit()
    setup_seconds = perf_counter()-started

    def assert_waiting():
        with Session(engine) as db:
            activate_waiting_plans(db)
            assert db.get(m.LabelingPlan, child_id).status == "waiting"
            assert claim_primary_block(db, child_id, team, person) is None
            assert db.scalar(select(func.count()).select_from(m.LabelingJob).where(
                m.LabelingJob.plan_id == child_id)) == 0

    # One 17-frame video job remains; images must not be assigned yet.
    assert_waiting()
    with Session(engine) as db:
        last_video = claim_primary_block(db, parent_id, team, next_person)
        assert last_video is not None and last_video.dataset_id == video_ds
        assert last_video.frame_count == 17
        # A rolled-back reservation must not exhaust the queue.
        db.rollback()
    assert_waiting()
    with Session(engine) as db:
        last_video = claim_primary_block(db, parent_id, team, next_person)
        assert last_video is not None and last_video.frame_count == 17
        last_video_id = last_video.id
        db.commit()
    transition_started = perf_counter()
    with Session(engine) as db:
        # Request through the original queue, with the last videos still open.
        image_job = claim_primary_block(db, parent_id, other_team, image_person)
        assert image_job is not None and image_job.frame_count == 30 and image_job.video_count == 0
        assert image_job.dataset_id == image_ds and image_job.plan_id == child_id
        assert set(image_job.media_ids).issubset(photos)
        assert claim_primary_block(db, parent_id, team, next_person) is None
        assert claim_primary_block(db, child_id, team, person) is None
        assert claim_primary_block(db, child_id, other_team, image_person) is None
        db.commit()
        transition_seconds = perf_counter()-transition_started
        activate_waiting_plans(db)  # Restart/repeated poll is idempotent.
        assert db.get(m.LabelingPlan, child_id).status == "running"
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.target_id == child_id, m.AuditEvent.action == "labeling.plan.sequence.start")) == 1
        assert db.scalar(select(func.count()).select_from(m.ReviewSample).where(
            m.ReviewSample.event_id == event)) == 0
        assert db.get(m.WorkPacket, last_video_id).submitted_at is None
        assert db.get(m.WorkPacket, jobs[-1]["id"]).submitted_at is None
        assert db.get(m.LabelingPlan, parent_id).dataset_id == video_ds
        assert [(b.id, b.media_ids) for b in db.scalars(select(m.WorkBlock).where(
            m.WorkBlock.plan_id == parent_id).order_by(m.WorkBlock.ordinal))] == original_blocks
        assert db.scalar(select(func.count()).select_from(m.LabelingJob).where(
            m.LabelingJob.plan_id == parent_id)) == 1682
    print(json.dumps({"video_frames": total, "video_jobs": 1682, "last_video_job_frames": 17,
        "separate_mounted_images": len(photos), "blocked_at_assigned_frames": total-17,
        "activated_at_assigned_frames": total, "activation_count": 1,
        "first_image_job_frames": 30, "setup_seconds": round(setup_seconds,3),
        "handoff_and_claim_seconds": round(transition_seconds,3), "real_remote_jobs": 0}))
    engine.dispose()
