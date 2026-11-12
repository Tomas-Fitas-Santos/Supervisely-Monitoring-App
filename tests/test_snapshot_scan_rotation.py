"""A blocked snapshot job must not starve later teams and submissions."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from annotatiathon import models as m
from annotatiathon.native_worker import (
    _annotation_snapshot_cursor,
    next_annotation_snapshot_candidates,
)


def test_annotation_scan_advances_past_unavailable_jobs_and_wraps():
    engine = create_engine("sqlite+pysqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        event = m.Event(name="Scan fairness")
        db.add(event)
        db.flush()
        dataset = m.MediaDataset(event_id=event.id, name="Media",
                                 upload_kind="folder", root_path="/not-yet-synced",
                                 created_by="operator", status="ready")
        db.add(dataset)
        db.flush()
        plan = m.LabelingPlan(event_id=event.id, dataset_id=dataset.id,
                              jobs_per_team=0, redundancy=1, created_by="operator")
        db.add(plan)
        db.flush()
        plan_id = plan.id
        team = m.CompetitionTeam(event_id=event.id, name="Competitors")
        db.add(team)
        db.flush()
        for i in range(18):
            db.add(m.LabelingJob(
                id=f"job-{i:03}", event_id=event.id, dataset_id=dataset.id,
                plan_id=plan.id, team_id=team.id, sequence=i + 1,
                assignee_person_id="participant", media_ids=[f"media-{i}"],
                frame_count=1, video_count=0, morphology_counts={},
                status="on_review",
            ))
    try:
        with Session(engine) as db:
            plan = db.get(m.LabelingPlan, plan_id)
            first = next_annotation_snapshot_candidates(db, plan)
            assert len(first) == 10
            _annotation_snapshot_cursor[plan_id] = first[-1].id
            second = next_annotation_snapshot_candidates(db, plan)
            assert len(second) == 8
            assert not {item.id for item in first} & {item.id for item in second}
            _annotation_snapshot_cursor[plan_id] = second[-1].id
            again = next_annotation_snapshot_candidates(db, plan)
            assert [x.id for x in again] == [x.id for x in first]
    finally:
        _annotation_snapshot_cursor.pop(plan_id, None)
        engine.dispose()
