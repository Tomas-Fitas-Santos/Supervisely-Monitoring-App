"""Bundled real-account rehearsal inputs; email addresses are never credentials."""
import json
from pathlib import Path

from .service import WorkflowError

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'organiser-demo'


def accounts():
    return json.loads((ROOT / 'accounts.json').read_text(encoding='utf-8'))


def import_roster(sessions, actor):
    from .registration import COLUMNS, Registrations
    return Registrations(sessions).import_csv(
        actor, (ROOT / 'registrations.csv').read_text(encoding='utf-8'), {k: k for k in COLUMNS})


def upload_photos(setup, gateway, actor, operation_id):
    """An explicit organiser action uploads to the configured source workspace."""
    from .db import SourceOrigin
    from .setup import pilot_meta
    workspace = setup.groups()['workspace_id']
    if not workspace:
        raise WorkflowError('Create the event groups and source workspace first.')
    sources = gateway.sources(setup.monitoring_team_id)
    if gateway.inventory(setup.monitoring_team_id, [d['id'] for d in sources['datasets']], 1)[0]:
        raise WorkflowError('Source media is already available. Select the existing datasets or upload additional files explicitly.')
    paths = [(p.relative_to(ROOT / 'dataset').as_posix(), p)
             for p in sorted((ROOT / 'dataset' / 'images').rglob('*.jpg'))]
    with setup.operation(actor, operation_id, 'bundled_source_upload', {}) as checkpoint:
        result = gateway.upload(setup.monitoring_team_id, workspace,
                                'CC0 demonstration birds', 'images', paths, pilot_meta(), checkpoint)
        with setup.sessions.begin() as session:
            for origin in result['sources']:
                session.merge(SourceOrigin(**origin))
    return result
