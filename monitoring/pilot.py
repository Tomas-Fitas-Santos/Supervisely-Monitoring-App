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


def prepare_sample(setup, gateway, actor, operation_id, monitor_name='Demo Monitors',
                   annotator_name='Demo Annotators', existing_monitor=None, existing_annotator=None):
    """Explicit live setup; never accept invitations, freeze rules or invent work.

    Each native write uses the normal durable setup operations. A retry reuses
    confirmed team IDs/pending invitations and previously uploaded sample files.
    An uncertain write still requires inspection through the normal recovery UI.
    """
    import hashlib
    from .db import EventClosure
    from .onboarding import EmailOnboarding
    from .registration import Registrations
    from .setup import KEY

    if not isinstance(operation_id, str) or not KEY.fullmatch(operation_id) or len(operation_id) > 70:
        raise WorkflowError('Reload the demo setup before trying again.')
    setup.authorize(gateway, actor)
    with setup.sessions() as session:
        if session.get(EventClosure, 1):
            raise WorkflowError('This event is closed. Its records cannot be reset by sample setup.')
    if not setup.groups()['ready']:
        setup.configure_groups(gateway, actor, operation_id + '-groups', monitor_name, annotator_name,
                               existing_monitor, existing_annotator)
    import_roster(setup.sessions, actor)
    registrations = Registrations(setup.sessions)
    # Only the bundled rows are provisioned; other registrations remain manual.
    names = {r['team_name'] for r in registrations.columns(
        (ROOT / 'registrations.csv').read_text(encoding='utf-8'))[1]}
    for row in registrations.rows():
        if row['team_name'] in names and row['membership_status'] != 'ready':
            EmailOnboarding(setup.sessions).provision(gateway, setup, actor, row['id'], row['monitor_id'])
    sources = gateway.sources(setup.monitoring_team_id)
    inventory = gateway.inventory(setup.monitoring_team_id, [d['id'] for d in sources['datasets']], 1)[0]
    if inventory:
        expected = {(p.relative_to(ROOT / 'dataset').as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in (ROOT / 'dataset/images').rglob('*.jpg')}
        observed = {(a.get('relative_path'), a.get('checksum')) for a in inventory}
        if not expected.issubset(observed):
            raise WorkflowError('Source media is already available and does not match the bundled sample. '
                                'Your teams were retained; select/upload sources manually in Dataset.')
    else:
        upload_photos(setup, gateway, actor, operation_id + '-photos')


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
