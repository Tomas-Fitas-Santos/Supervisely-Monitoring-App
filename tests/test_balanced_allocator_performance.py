"""Bounded claim hydration and compatibility with the original allocator."""
import random
from collections import Counter
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

from annotatiathon.balanced_queue import allocate_balanced_claim
from annotatiathon.models import BalancedVideoRun, Base, MediaAsset


def legacy_selection(rows, sealed=False):
    projected, species = Counter(), Counter()
    for row in rows:
        if row['next_offset']:
            projected[row['morphology_group']] += row['frame_count']
            species[row['morphology_group'], row['source_folder']] += 1
    selected, slots = [], 30
    tails = sorted((r for r in rows if 0 < r['next_offset'] < r['frame_count']),
                   key=lambda r: (r['frame_count'] - r['accepted_count'],
                                  r['frame_count'] - r['next_offset'],
                                  r['selection_order'], r['id']))
    for row in tails:
        if not slots:
            break
        count = min(slots, row['frame_count'] - row['next_offset'])
        selected.append((row, row['next_offset'], count))
        slots -= count
    while slots and not sealed:
        used = {r['id'] for r, _, _ in selected}
        candidates = [r for r in rows if not r['next_offset'] and r['id'] not in used]
        if not candidates:
            break
        row = min(candidates, key=lambda r: (
            projected[r['morphology_group']], species[r['morphology_group'], r['source_folder']],
            r['morphology_group'], r['source_folder'], r['selection_order'], r['id']))
        count = min(slots, row['frame_count'])
        selected.append((row, 0, count))
        slots -= count
        projected[row['morphology_group']] += row['frame_count']
        species[row['morphology_group'], row['source_folder']] += 1
    return selected


def run_row(i, **kw):
    return dict(id=f'r{i:06}', event_id='event', dataset_id='dataset', plan_id='plan',
                video_id=f'v{i:06}', video_group=f'clip{i}', source_folder='species',
                morphology_group='group', first_ordinal=41, frame_count=60,
                next_offset=0, accepted_count=0, selection_order=i, **kw)


def exercise(rows, sealed=False):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    expected = legacy_selection(rows, sealed)
    with engine.begin() as conn:
        if rows:
            conn.execute(insert(BalancedVideoRun), rows)
        assets = [{'id': f"{r['id']}:{offset}", 'event_id': 'event', 'dataset_id': 'dataset',
                       'video_id': r['video_id'], 'video_group': r['video_group'],
                       'frame_index': 41 + offset, 'relative_path': f"{r['id']}/{offset}.jpg",
                       'morphology': r['source_folder'], 'kind': 'video_frame',
                       'size_bytes': 1, 'sha256': '0' * 64}
                  for r, start, count in expected for offset in range(start, start + count)]
        if assets:
            conn.execute(insert(MediaAsset), assets)
    loaded, statements = [], []
    with Session(engine) as db:
        event.listen(db, 'loaded_as_persistent', lambda session, obj: loaded.append(obj))
        event.listen(engine, 'before_cursor_execute',
                     lambda conn, cur, statement, params, context, many: statements.append(statement))
        job = allocate_balanced_claim(db, SimpleNamespace(id='plan', dataset_id='dataset',
                                      event_id='event'), SimpleNamespace(end_at=0 if sealed else None),
                                      'team', 'person')
        actual = job.media_ids if job else []
        assert actual == [asset['id'] for asset in assets]
        assert len(actual) == len(set(actual)) <= 30
        db.rollback()
    engine.dispose()
    return loaded, statements


def test_claim_does_not_hydrate_inventory():
    loaded, statements = exercise([run_row(i) for i in range(10000)])
    assert sum(isinstance(row, BalancedVideoRun) for row in loaded) <= 30
    assert sum(isinstance(row, MediaAsset) for row in loaded) <= 30
    assert len(statements) <= 15


@pytest.mark.parametrize('seed', range(30))
def test_randomized_claim_matches_original_selection(seed):
    rng = random.Random(seed)
    rows = []
    for i in range(rng.randrange(1, 120)):
        row = run_row(i)
        row['frame_count'] = rng.randrange(1, 80)
        row['next_offset'] = rng.choice([0, 0, rng.randrange(row['frame_count'] + 1)])
        row['accepted_count'] = rng.randrange(row['next_offset'] + 1)
        row['morphology_group'] = rng.choice(['A', 'B', 'C'])
        row['source_folder'] = rng.choice(['a', 'b', 'c', 'd'])
        row['selection_order'] = rng.randrange(10)
        rows.append(row)
    exercise(rows, sealed=seed % 3 == 0)


def test_tiny_unopened_videos_fill_thirty_and_final_remainder():
    rows = [run_row(i) for i in range(35)]
    for row in rows:
        row['frame_count'] = 1
    exercise(rows)
    exercise(rows[:7])


def test_empty_and_finish_sealed_unopened_inventory():
    exercise([])
    exercise([run_row(0)], sealed=True)
