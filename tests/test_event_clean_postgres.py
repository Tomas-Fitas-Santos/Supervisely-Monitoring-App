"""Cleanup invariants on an explicitly disposable, migrated PostgreSQL database."""
import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.event_clean import worker_reset_barrier
from annotatiathon.full_reset_api import clear_event_data

pytestmark = pytest.mark.skipif(not os.getenv("NIGHTJAR_QUEUE_TEST_URL"),
                               reason="requires disposable PostgreSQL")


def test_reset_waits_for_workers_and_releases_lock_after_failure():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    try:
        with worker_reset_barrier(engine) as publisher:
            assert publisher
            with worker_reset_barrier(engine) as validator:
                assert validator  # Normal workers continue concurrently.
                with worker_reset_barrier(engine, reset=True) as reset:
                    assert not reset
            with worker_reset_barrier(engine, reset=True) as reset:
                assert not reset  # Publisher still holds its session lock.
        with (pytest.raises(RuntimeError, match="simulated crash"),
              worker_reset_barrier(engine, reset=True) as reset):
            assert reset
            with worker_reset_barrier(engine) as publisher:
                assert not publisher
            with worker_reset_barrier(engine, reset=True) as duplicate_reset:
                assert not duplicate_reset
            raise RuntimeError("simulated crash")
        with worker_reset_barrier(engine, reset=True) as retry:
            assert retry
        with worker_reset_barrier(engine) as resumed:
            assert resumed
    finally:
        engine.dispose()


def test_clean_deletes_linked_plans_with_real_foreign_keys_preserving_inventory():
    engine = create_engine(os.environ["NIGHTJAR_QUEUE_TEST_URL"])
    suffix = m.identifier()[:8]
    event, staff, team, person, video_ds, image_ds, parent, child, video, asset, job = [
        f"clean-{kind}-{suffix}" for kind in (
            "event", "staff", "team", "person", "videos", "images", "parent", "child", "clip", "asset", "job")]
    try:
        with Session(engine) as db, db.begin():
            db.add(m.Event(id=event, name="Disposable clean test"))
            db.add(m.StaffAccount(id=staff, name="Test", email=f"{staff}@example.test",
                                  password_hash="disabled", roles=["organiser"]))
            db.flush()
            db.add(m.CompetitionTeam(id=team, event_id=event, name="Test", table_number=3))
            for dsid, kind in ((video_ds, "zip"), (image_ds, "mounted")):
                db.add(m.MediaDataset(id=dsid, event_id=event, name=dsid, upload_kind=kind,
                                      root_path="/test", status="ready", created_by=staff))
            db.flush()
            db.add(m.Person(id=person, event_id=event, team_id=team, name="Test",
                email=f"{person}@example.test", kind="participant", membership_status="joined",
                supervisely_user_id=100, native_verified_at=1, check_in_at=1, table_number=3))
            db.add(m.MediaVideo(id=video, event_id=event, dataset_id=video_ds,
                video_group="unknown/clip", extraction_policy="all_frames", frame_count=1))
            db.add(m.LabelingPlan(id=parent, event_id=event, dataset_id=video_ds,
                status="running", jobs_per_team=0, redundancy=1, created_by=staff, queue_seed=suffix))
            db.flush()
            db.add(m.LabelingPlan(id=child, event_id=event, dataset_id=image_ds,
                status="waiting", jobs_per_team=0, redundancy=1, created_by=staff,
                queue_seed=suffix, allocation_mode="images_30_v1", after_plan_id=parent))
            db.add(m.MediaAsset(id=asset, event_id=event, dataset_id=video_ds, video_id=video,
                relative_path="videos/unknown/clip/1.jpg", kind="video_frame", morphology="unknown",
                size_bytes=1, sha256="0"*64))
            db.flush()
            db.add(m.NativeSourceMedia(event_id=event, dataset_id=video_ds, media_id=asset,
                native_dataset_id=100, native_image_id=1000000000+int(suffix[:6], 16)))
            db.add(m.LabelingJob(id=job, event_id=event, dataset_id=video_ds, plan_id=parent,
                team_id=team, sequence=1, assignee_person_id=person, release_mode="shared_primary",
                status="in_progress", media_ids=[asset], frame_count=1, video_count=1, morphology_counts={}))
            db.flush()
            db.add(m.WorkBlock(event_id=event, plan_id=parent, video_id=video,
                ordinal=1, frame_count=1, media_ids=[asset], repeat_media_ids=[], job_id=job))
            db.add(m.WorkPacket(event_id=event, job_id=job, person_id=person))
            db.add(m.ReviewSample(event_id=event, job_id=job, team_id=team, media_id=asset,
                correction_round=0, validated_complete=True, snapshot={}))
        with Session(engine) as db, db.begin():
            clear_event_data(db, event, preserve_datasets=True)
        with Session(engine) as db:
            assert db.get(m.LabelingPlan, parent) is None and db.get(m.LabelingPlan, child) is None
            assert db.get(m.LabelingJob, job) is None
            assert db.get(m.MediaDataset, video_ds).upload_kind == "zip"
            assert db.get(m.MediaDataset, image_ds).upload_kind == "mounted"
            assert db.get(m.MediaAsset, asset) is not None
            assert db.get(m.MediaVideo, video) is not None
            assert db.scalar(select(m.NativeSourceMedia).where(m.NativeSourceMedia.media_id == asset))
            participant = db.get(m.Person, person)
            assert participant.membership_status == "joined" and participant.native_verified_at == 1
            assert participant.team_id == team and participant.supervisely_user_id == 100
            assert participant.check_in_at is None and participant.table_number is None
            assert db.get(m.CompetitionTeam, team).table_number is None
    finally:
        engine.dispose()
