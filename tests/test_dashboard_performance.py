"""Large event screens keep query counts bounded and preserve monitor scoping."""
from sqlalchemy import event

from monitoring.db import Batch, Review, Team, TaskProgress
from monitoring.setup import Setup


def test_large_event_views_batch_database_reads_and_scope_reviews(service):
    with service.sessions.begin() as s:
        for i in range(100, 200):
            s.add(Team(id=i, name=f'Pair {i}', monitor_id=90, annotator_ids=[11, 12]))
            s.flush()
            s.add(Batch(id=f'batch-{i}', team_id=i, position=1, kind='images',
                        dataset_id=31, assets=[], job_ids=[i]))
            s.flush()
            s.add(Review(batch_id=f'batch-{i}', entity_id=i, frame_index=-1,
                         decision='correction', note=f'Fix {i}', monitor_id=90))
            s.add(TaskProgress(job_id=i, batch_id=f'batch-{i}', details={'job_id':i}))
    engine = service.sessions.kw['bind']
    queries = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        snapshot = service.snapshot(90)
        assert len(queries) <= 8
        assert len(snapshot['teams']) == 101
        assert all(t['monitor_id'] == 90 for t in snapshot['teams'])
        batch = next(t for t in snapshot['teams'] if t['id'] == 150)['batches'][0]
        assert batch['open_flags'] == 1
        assert batch['reviews'][0]['note'] == 'Fix 150'
        assert batch['jobs'] == [{'job_id':150}]
        queries.clear()
        directory = Setup(service.sessions, 10).directory()
        assert len(queries) <= 12
        assert len(directory['teams']) == 102
        assert all(t['has_batches'] for t in directory['teams'])
        assert all(t['can_assign'] and t['can_unassign'] for t in directory['teams'])
    finally:
        event.remove(engine, 'before_cursor_execute', record)
