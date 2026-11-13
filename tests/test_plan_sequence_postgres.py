"""Concurrent handoff runs only against an explicitly disposable PostgreSQL DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.plan_sequence import activate_waiting_plans
from annotatiathon.work_queue import claim_primary_block


@pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"), reason="requires disposable PostgreSQL")
def test_concurrent_handoff_and_image_claims():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    suffix = m.identifier()[:8]
    event, staff, team, video_ds, image_ds, parent, child, video = [f"{s}-{suffix}" for s in (
        "seq-event", "seq-staff", "seq-team", "seq-video", "seq-image", "seq-parent", "seq-child", "seq-source")]
    people = [f"seq-person-{suffix}-{i}" for i in range(2)]
    frame = f"seq-frame-{suffix}"
    image_ids = [f"seq-image-{suffix}-{i}" for i in range(31)]
    source_job = f"seq-job-{suffix}"
    with Session(engine) as db, db.begin():
        db.add(m.Event(id=event, name=f"Sequence test {suffix}"))
        db.add(m.StaffAccount(id=staff, email=f"{staff}@example.test", name="Audit", password_hash="disabled", roles=["organiser"]))
        db.flush()
        db.add(m.CompetitionTeam(id=team, event_id=event, name="Audit team"))
        for dsid in (video_ds, image_ds):
            db.add(m.MediaDataset(id=dsid, event_id=event, name=dsid, upload_kind="mounted",
                root_path="/test", status="ready", created_by=staff,
                frame_count=1 if dsid == video_ds else 0,
                image_count=31 if dsid == image_ds else 0,
                native_dataset_id=42, inventory_revision=1, mirrored_revision=1))
        db.flush()
        db.add(m.MediaVideo(id=video, event_id=event, dataset_id=video_ds,
            video_group="bird/clip", extraction_policy="all_frames", frame_count=1))
        db.add(m.LabelingPlan(id=parent, event_id=event, dataset_id=video_ds, jobs_per_team=0,
            redundancy=1, status="running", created_by=staff, queue_seed=suffix, allocation_mode="shared_30_v1"))
        db.flush()
        db.add(m.LabelingPlan(id=child, event_id=event, dataset_id=image_ds, jobs_per_team=0,
            redundancy=1, status="waiting", created_by=staff, queue_seed=suffix,
            allocation_mode="images_30_v1", after_plan_id=parent))
        for i, person in enumerate(people):
            db.add(m.Person(id=person, event_id=event, team_id=team, name=person,
                email=f"{person}@example.test", kind="participant", membership_status="joined",
                supervisely_user_id=i+1, supervisely_role="annotator", native_verified_at=1, check_in_at=1))
        db.add(m.MediaAsset(id=frame, event_id=event, dataset_id=video_ds, video_id=video,
            relative_path="videos/bird/clip/frame.jpg", kind="video_frame", morphology="bird",
            video_group="bird/clip", frame_index=1, size_bytes=1, sha256="0"*64))
        for mid in image_ids:
            db.add(m.MediaAsset(id=mid, event_id=event, dataset_id=image_ds, relative_path=f"images/bird/{mid}.jpg",
                kind="image", morphology="bird", size_bytes=1, sha256="0"*64))
        db.flush()
        db.add(m.LabelingJob(id=source_job, event_id=event, dataset_id=video_ds, plan_id=parent,
            team_id=team, sequence=1, assignee_person_id=people[0], release_mode="shared_primary",
            status="on_review", media_ids=[frame], frame_count=1, video_count=1, morphology_counts={"bird":1}))
        for i, mid in enumerate(image_ids):
            db.add(m.NativeSourceMedia(event_id=event, dataset_id=image_ds, media_id=mid,
                native_dataset_id=42, native_image_id=10000000+int(suffix[:5],16)*32+i))
        db.add(m.NativeOperation(event_id=event, kind="mirror_dataset", target_id=image_ds, status="done"))
        db.add(m.WorkBlock(event_id=event, plan_id=child, video_id=None, ordinal=1, frame_count=30,
            media_ids=image_ids[:30], repeat_media_ids=[]))
        db.add(m.WorkBlock(event_id=event, plan_id=child, video_id=None, ordinal=2, frame_count=1,
            media_ids=image_ids[30:], repeat_media_ids=[]))
        db.flush()
        db.add(m.WorkBlock(event_id=event, plan_id=parent, video_id=video, ordinal=1,
            frame_count=1, media_ids=[frame], repeat_media_ids=[], job_id=source_job))
        db.add(m.WorkPacket(event_id=event, job_id=source_job, person_id=people[0], submitted_at=1))
        db.add(m.ReviewSample(event_id=event, job_id=source_job, team_id=team, media_id=frame,
            correction_round=0, validated_complete=True, snapshot={}))
    barrier = Barrier(2)

    def activate():
        with Session(engine) as db:
            barrier.wait(timeout=10)
            activate_waiting_plans(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: activate(), range(2)))
    with Session(engine) as db:
        assert db.get(m.LabelingPlan, child).status == "running"
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.target_id == child, m.AuditEvent.action == "labeling.plan.sequence.start")) == 1
        assert db.get(m.LabelingPlan, parent).status == "running"
    barrier = Barrier(2)

    def claim(person):
        with Session(engine) as db, db.begin():
            barrier.wait(timeout=10)
            job = claim_primary_block(db, child, team, person)
            assert job is not None and job.video_count == 0
            return job.media_ids

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, people))
    assert sorted(map(len, claims)) == [1, 30]
    assert set(claims[0]).isdisjoint(claims[1])
    engine.dispose()
