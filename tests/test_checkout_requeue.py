"""Operational return preserves reviewed work, immutable sources, and queue rules."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_native_sync import context

from annotatiathon import models as m
from annotatiathon.checkout_requeue import check_job, finish_returns, prepare_job
from annotatiathon.native_worker import next_annotation_snapshot_candidates, source_media_for_job, sync_one
from annotatiathon.work_queue import claim_primary_block


@pytest.fixture
def returns(context, tmp_path):
    _, maker, _ = context
    with maker.begin() as db:
        owner = db.scalar(select(m.StaffAccount))
        ds = m.MediaDataset(event_id=m.DEFAULT_EVENT_ID, name="Video", upload_kind="folder",
                            status="ready", root_path=str(tmp_path), created_by=owner.id, frame_count=75)
        team = m.CompetitionTeam(event_id=m.DEFAULT_EVENT_ID, name="Departed")
        recipient = m.CompetitionTeam(event_id=m.DEFAULT_EVENT_ID, name="Present")
        db.add_all([ds, team, recipient]); db.flush()
        plan = m.LabelingPlan(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id, created_by=owner.id,
                              status="running", jobs_per_team=0, redundancy=1,
                              allocation_mode="shared_30_v1", queue_seed="queue")
        video = m.MediaVideo(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id,
                             video_group="a/source", extraction_policy="all_frames")
        db.add_all([plan, video]); db.flush()
        people = []
        for i in range(3):
            p = m.Person(event_id=m.DEFAULT_EVENT_ID, name=f"Person {i}", email=f"p{i}@example.org",
                         kind="participant", team_id=team.id if i<2 else recipient.id,
                         check_in_at=None if i<2 else 1, membership_status="joined",
                         native_verified_at=1, supervisely_role="annotator", supervisely_user_id=100+i)
            db.add(p); people.append(p)
        assets = [m.MediaAsset(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id, video_id=video.id,
                               video_group="a/source", frame_index=i, relative_path=f"{i}.jpg",
                               kind="video_frame", morphology="birds", sha256="a"*64, size_bytes=1)
                  for i in range(75)]
        db.add_all(assets); db.flush()
        for i, asset in enumerate(assets):
            db.add(m.NativeSourceMedia(event_id=m.DEFAULT_EVENT_ID,dataset_id=ds.id,
                                       media_id=asset.id,native_dataset_id=200,native_image_id=i+1))
        jobs = []
        for i in range(2):
            ids = [a.id for a in assets[i*30:(i+1)*30]]
            job = m.LabelingJob(event_id=m.DEFAULT_EVENT_ID, dataset_id=ds.id, plan_id=plan.id,
                                team_id=team.id, assignee_person_id=people[i].id, sequence=i+1,
                                native_job_number=i+1, release_mode="shared_primary", media_ids=ids,
                                frame_count=30, video_count=1, morphology_counts={"birds":30},
                                status="in_progress", native_dataset_id=200)
            db.add(job); db.flush(); jobs.append(job)
            db.add(m.WorkBlock(event_id=m.DEFAULT_EVENT_ID, plan_id=plan.id, video_id=video.id,
                               ordinal=i+1, frame_count=30, media_ids=ids, repeat_media_ids=[], job_id=job.id))
            db.add(m.WorkPacket(job_id=job.id, event_id=m.DEFAULT_EVENT_ID, person_id=people[i].id))
            db.add(m.NativeJobBinding(event_id=m.DEFAULT_EVENT_ID, job_id=job.id,
                                      person_id=people[i].id, native_job_id=1000+i))
            for j, mid in enumerate(ids):
                db.add(m.NativeMediaInstance(event_id=m.DEFAULT_EVENT_ID, job_id=job.id,
                    media_id=mid, native_dataset_id=200, native_image_id=i*30+j+1))
        db.add(m.WorkBlock(event_id=m.DEFAULT_EVENT_ID, plan_id=plan.id, video_id=video.id,
                           ordinal=3, frame_count=15, media_ids=[a.id for a in assets[60:]], repeat_media_ids=[]))
        for a in assets[:3]:
            db.add(m.ReviewSample(event_id=m.DEFAULT_EVENT_ID, job_id=jobs[0].id, team_id=team.id,
                                  media_id=a.id, correction_round=0, validated_complete=True,
                                  snapshot={"untouched":True}))
        native = {1000+i:SimpleNamespace(id=1000+i, dataset_id=200, assigned_to_id=100+i,
                                         status="in_progress", disabled=False) for i in range(2)}
        ids = {"plan":plan.id,"jobs":[j.id for j in jobs],"people":[p.id for p in people],
               "recipient":recipient.id,"source":{a.id for a in assets}}
    calls = []
    complete = {"objects":[{"classTitle":title,"geometryType":"rectangle" if title=="bird" else "point"}
                            for title in ("bird","left_eye","right_eye","beak","crown")]}
    def stop(nid):
        calls.append(("stop",nid)); native[nid].status="stopped"
    def archive(nid):
        calls.append(("archive",nid)); native[nid].disabled=True
    def annotations(nid, image_infos, project_meta):
        return [SimpleNamespace(to_json=lambda value=complete if i.id in (4,5) else {"objects":[]}:value)
                for i in image_infos]
    api = SimpleNamespace(labeling_job=SimpleNamespace(
        get_info_by_id=lambda nid:native[nid], stop=stop, archive=archive,
        get_project_meta=lambda nid:None, get_annotations=annotations),
        image=SimpleNamespace(get_list=lambda dataset,filters:[SimpleNamespace(id=nid)
            for nid in filters[0]["value"]]))
    return maker, api, ids, calls, tmp_path


def test_return_preserves_accepted_frames_and_reclaims_only_pending_once(returns):
    maker, api, ids, calls, path = returns
    with maker() as db:
        assert prepare_job(db,api,m.DEFAULT_EVENT_ID,1)["retained_complete_frames"] == 5
        assert prepare_job(db,api,m.DEFAULT_EVENT_ID,2)["returned_frames"] == 30
        result = finish_returns(db,m.DEFAULT_EVENT_ID,[1,2])
        assert result["returned_frames"] == 55 and result["available_frames"] == 70
        assert result["retained_complete_frames"] == 5 and result["coverage_preserved"]
        assert result["released_obsolete_references"] == 55
        assert finish_returns(db,m.DEFAULT_EVENT_ID,[1,2]) == {"status":"already_done"}
        assert prepare_job(db,api,m.DEFAULT_EVENT_ID,1)["already_prepared"]
        samples = list(db.scalars(select(m.ReviewSample)))
        assert len(samples) == 5 and all(s.team_id == db.get(m.LabelingJob,ids["jobs"][0]).team_id for s in samples)
        assert sum(s.snapshot.get("untouched",False) for s in samples) == 3
        assert db.scalar(select(func.count()).select_from(m.WorkPacket).where(m.WorkPacket.submitted_at.is_(None))) == 0
        # Stale status polling must not revive either archived attempt.
        for jid in ids["jobs"]:
            assert sync_one(db,api,db.get(m.LabelingJob,jid)) == "completed"
        assert [db.get(m.LabelingJob,jid).frame_count for jid in ids["jobs"]] == [5,0]
        assert next_annotation_snapshot_candidates(db,db.get(m.LabelingPlan,ids["plan"])) == []
        blocks = list(db.scalars(select(m.WorkBlock)))
        assert {mid for b in blocks for mid in b.media_ids} == ids["source"]
        available = sorted((b for b in blocks if b.job_id is None),key=lambda b:b.ordinal)
        assert [b.frame_count for b in available] == [30,30,10]
        claimed = claim_primary_block(db,ids["plan"],ids["recipient"],ids["people"][2])
        assert claimed and claimed.frame_count == 30
        assert set(claimed.media_ids).isdisjoint(s.media_id for s in samples)
        db.commit()
        # Actual publisher mapping must be able to reuse the returned mirrored
        # image IDs; obsolete attempt mappings otherwise violate global uniqueness.
        assert len(source_media_for_job(db,claimed,200)) == 30
    assert len(calls) == 4
    assert len(list(path.rglob("checkout-requeue-backup.json"))) == 2


def test_present_team_is_never_stopped(returns):
    maker, api, ids, calls, _ = returns
    with maker.begin() as db:
        db.get(m.Person,ids["people"][1]).check_in_at=1
    with maker() as db, pytest.raises(ValueError,match="team_has_present_members"):
        prepare_job(db,api,m.DEFAULT_EVENT_ID,1)
    assert calls == []


def test_remote_submission_race_is_not_archived(returns):
    maker, api, _, calls, _ = returns
    api.labeling_job.get_info_by_id(1000).status="on_review"
    with maker() as db, pytest.raises(ValueError,match="native_job_already_submitted"):
        prepare_job(db,api,m.DEFAULT_EVENT_ID,1)
    assert calls == []


def test_unconfirmed_stop_blocks_return_and_parks_for_reconciliation(returns):
    maker, api, ids, _, _ = returns
    api.labeling_job.stop=lambda nid:None
    with maker() as db, pytest.raises(ValueError,match="native_stop_not_confirmed"):
        prepare_job(db,api,m.DEFAULT_EVENT_ID,1)
    with maker() as db:
        assert db.get(m.LabelingJob,ids["jobs"][0]).status == "needs_correction"
        op=db.scalar(select(m.NativeOperation).where(m.NativeOperation.kind=="checkout_requeue"))
        assert op.status == "attention"
        assert db.scalar(select(func.count()).select_from(m.WorkBlock)) == 3
