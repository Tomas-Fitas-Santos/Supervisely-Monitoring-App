from collections import Counter

import pytest

from monitoring.importer import Manifest, import_manifest
from monitoring.planner import allocate


def test_counts_are_driven_by_input_and_every_team_gets_a_whole_video():
    teams = list(range(1, 9))
    videos = [{'source_id': f'v{i}', 'kind': 'video', 'units': 50 + i} for i in range(2)]
    images = [{'source_id': f'i{i}', 'kind': 'images', 'units': 1} for i in range(48)]
    result = allocate(teams, videos + images, batch_units=8)
    coverage = Counter()
    for team in result.values():
        assert team['video'] in videos
        coverage[team['video']['source_id']] += 1
        for batch in team['image_batches']:
            assert sum(a['units'] for a in batch) <= 8
            coverage.update(a['source_id'] for a in batch)
    assert set(result) == set(teams)
    assert all(n >= 3 for n in coverage.values())
    assert all(coverage[a['source_id']] == 3 for a in images)
    assert max(t['units'] for t in result.values()) - min(t['units'] for t in result.values()) <= 1


def test_impossible_video_replication_is_reported():
    with pytest.raises(ValueError, match='Too few teams'):
        allocate([1, 2, 3], [{'source_id': f'v{i}', 'kind': 'video', 'units': 20} for i in range(2)], batch_units=10)


def test_no_default_batch_throughput_assumption():
    with pytest.raises(ValueError, match='Batch size'):
        allocate([1, 2, 3], [{'source_id': 'v', 'kind': 'video', 'units': 20}])


def example():
    import json
    from pathlib import Path
    return json.loads(Path('examples/manifest.json').read_text())


def test_manifest_rejects_duplicate_destination_entities():
    data = example()
    data['teams'][0]['batches'][0]['assets'][1]['entity_id'] = 501
    with pytest.raises(ValueError, match='distinct destination'):
        Manifest.model_validate(data)


def test_manifest_reports_under_replication_and_rejects_split_video():
    data = example()
    assert len(Manifest.model_validate(data).coverage()['under_replicated']) == 3
    data['teams'][0]['batches'][-1]['assets'].append({'source_id': 'second-video', 'entity_id': 602, 'frames': 100})
    with pytest.raises(ValueError, match='exactly one'):
        Manifest.model_validate(data)


def test_import_refuses_to_overwrite_live_event(service):
    with pytest.raises(ValueError, match='already contains'):
        import_manifest(service.sessions, Manifest.model_validate(example()), pilot=True)
