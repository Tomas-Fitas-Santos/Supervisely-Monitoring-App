import os

import pytest

from monitoring.db import Base, Batch, Team, database, initialize
from monitoring.service import Service


@pytest.fixture
def service(tmp_path):
    engine, sessions = database(os.getenv('TEST_DATABASE_URL', f'sqlite:///{tmp_path}/event.db'))
    Base.metadata.drop_all(engine)
    initialize(engine)
    with sessions.begin() as s:
        s.add_all([Team(id=1, name='Team A', monitor_id=90, annotator_ids=[11, 12]),
                   Team(id=2, name='Team B', monitor_id=91, annotator_ids=[21, 22])])
        s.flush()
        s.add_all([
            Batch(id='a1', team_id=1, position=1, kind='images', dataset_id=31,
                  assets=[{'source_id': 'bird1', 'entity_id': 101}, {'source_id': 'bird2', 'entity_id': 102}]),
            Batch(id='a2', team_id=1, position=2, kind='images', dataset_id=31,
                  assets=[{'source_id': 'bird3', 'entity_id': 103}]),
            Batch(id='av', team_id=1, position=3, kind='video', dataset_id=32,
                  assets=[{'source_id': 'video1', 'entity_id': 201, 'frames': 90}]),
            Batch(id='b1', team_id=2, position=1, kind='images', dataset_id=41,
                  assets=[{'source_id': 'bird1', 'entity_id': 301}]),
        ])
    yield Service(sessions)
    engine.dispose()
