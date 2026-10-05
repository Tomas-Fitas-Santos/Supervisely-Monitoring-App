import logging
import os
from pathlib import Path

import supervisely as sly
from fastapi import Request

from . import controller
from .dashboard import Dashboard
from .db import Review, database, initialize
from .gateway import Gateway
from .service import Service, WorkflowError
from .settings import Settings
from .setup import Setup
from .setup_gateway import SetupGateway

sly.env.enable_multiuser_app_mode()
settings = Settings.load()
engine, sessions = database(settings.database_url)
initialize(engine)
service = Service(sessions, settings.stale_after)
setup = Setup(sessions, settings.monitoring_team_id)
dashboard = Dashboard()
app = sly.Application(layout=dashboard, static_dir=str(Path(__file__).parent / 'static'))
log = logging.getLogger(__name__)


def identity(request):
    if settings.local:
        # Loopback smoke test only; the runner binds localhost in this mode.
        if request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise WorkflowError('Local development is only available on loopback.')
        api = sly.Api.from_env() if os.getenv('API_TOKEN') else None
        # Live local pilot identity always comes from the credential, never an arbitrary ID.
        uid = int(api.user.get_my_info().id) if api else int(os.environ['LOCAL_USER_ID'])
        if api:
            if api.server_address.rstrip('/') != settings.server_address:
                raise WorkflowError('Local API credential belongs to a different Supervisely server.')
            Gateway(api).member(settings.monitoring_team_id, uid)
        return uid, Gateway(api) if api else None
    # Require the current request's token, not a cached token from a prior user's request.
    api = getattr(request.state, 'api', None)
    uid = sly.env.user_from_multiuser_app()
    if api is None or uid is None:
        raise WorkflowError('Open this app through your Supervisely monitoring team.')
    if api.server_address.rstrip('/') != settings.server_address:
        raise WorkflowError('The session belongs to a different Supervisely server.')
    if int(api.user.get_my_info().id) != int(uid):
        raise WorkflowError('Session identity and API credential do not match.')
    gateway = Gateway(api)
    gateway.member(settings.monitoring_team_id, int(uid))
    return int(uid), gateway


def request_form(request):
    # Read the incoming payload, not SDK state shared by two tabs for the same user.
    state = getattr(request.state, 'state', None)
    if not isinstance(state, dict) or not isinstance(state.get('dashboard'), dict):
        raise WorkflowError('Missing dashboard request state. Reload the app and try again.')
    return dict(state['dashboard'])


@app.get_server().post('/dashboard/action')
def action(request: Request):
    uid = None
    try:
        uid, gateway = identity(request)
        f = request_form(request)
        name = f.get('action', 'refresh')
        message, links = '', []
        if name != 'refresh':
            team_id, batch_id = int(f['team_id']), str(f['batch_id'])
            team, batch = service.spec(uid, team_id, batch_id)
            if name == 'review':
                service.review(uid, team_id, batch_id, int(f['revision']), int(f['entity_id']),
                    int(f['frame_index']), f['decision'], f['note'])
                message = 'Review saved. Correction notes are internal monitoring records.'
            elif name == 'approve':
                service.approve(uid, team_id, batch_id, int(f['revision']), f['note'])
                message = 'Batch approved. The next task can now be released.'
            else:
                if gateway is None:
                    raise WorkflowError('Configure a Supervisely token to use live integration actions.')
                if name == 'sync':
                    controller.sync(service, gateway, team, batch, activity=True)
                    message = 'Progress refresh finished; check the sync status below.'
                elif name == 'release':
                    controller.release(service, gateway, uid, team_id, batch_id, int(f['revision']))
                    message = 'Task released to the participant team.'
                elif name == 'reconcile':
                    controller.reconcile(service, gateway, uid, team_id, batch_id)
                    message = 'Existing remote jobs reconciled.'
                elif name == 'open':
                    links = [{'id': jid, 'url': gateway.job_url(jid)} for jid in batch.job_ids]
                    message = 'Open a job below to inspect its live annotations in Supervisely.'
                elif name == 'publish':
                    from sqlalchemy import select
                    with sessions() as s:
                        review = s.scalar(select(Review).where(Review.batch_id == batch_id,
                            Review.entity_id == int(f['entity_id']), Review.frame_index == -1))
                    if review is None:
                        raise WorkflowError('Save an image review first.')
                    gateway.publish_image_review(team, batch, review.entity_id, review.decision)
                    message = 'Image decision published to Supervisely. Detailed notes remain in the app.'
                else:
                    raise WorkflowError('Unknown action.')
        can_setup = False
        if gateway:
            member = gateway.api.user.get_member_info_by_id(settings.monitoring_team_id, uid)
            can_setup = bool(member and member.role == 'admin')
        from supervisely.app import DataJson
        DataJson()['dashboard']['can_setup'] = can_setup
        if not can_setup:
            DataJson()['dashboard'].update(setup={}, catalog={}, plan_id=None)
        dashboard.display(service.snapshot(uid), message, links=links)
    except WorkflowError as e:
        # Authentication failures never retain another user's cached dashboard.
        if uid is None:
            from supervisely.app import DataJson
            DataJson()['dashboard'].update(setup={}, catalog={}, can_setup=False, plan_id=None, preview=None)
        dashboard.display(service.snapshot(uid) if uid is not None else {'teams': []}, str(e), True)
    except Exception:
        log.warning('Dashboard action failed; user must refresh or reconcile any pending release.')
        dashboard.display(service.snapshot(uid) if uid is not None else {'teams': []},
                          'Action failed. Refresh and check the task status before retrying.', True)


@app.get_server().post('/setup/action')
def setup_action(request: Request):
    from supervisely.app import DataJson
    uid = None
    gateway = None
    authorized = False
    try:
        uid, gateway = identity(request)
        gateway = SetupGateway(gateway.api) if gateway else None
        setup.authorize(gateway, uid)
        authorized = True
        f = request_form(request)
        name = f.get('setup_action', 'catalog')
        key = f.get('operation_id')
        message = ''
        data = DataJson()['dashboard']
        if name == 'catalog':
            data['catalog'] = gateway.catalog(settings.monitoring_team_id)
            message = 'Live Supervisely users and datasets loaded.'
        elif name == 'team':
            tid = setup.create_team(gateway, uid, key, f['name'], f['logins'], f['monitor_id'], f.get('existing_team_id'))
            message = f'Participant team {tid} registered and assigned to its monitor.'
        elif name == 'assign':
            setup.assign_monitor(gateway, uid, key, f['setup_team_id'], f['monitor_id'], f['setup_revision'])
            message = 'Monitor assignment saved. The previous monitor no longer has app access to this team.'
        elif name == 'chunk':
            data['upload_offset'] = setup.stage(uid, f['upload_id'], f['filename'], f['size'], f['offset'], f['chunk'])
        elif name == 'upload':
            result = setup.upload(gateway, uid, key, f['upload_ids'], f['workspace_id'], f['name'], f['kind'], f.get('meta'))
            data['catalog'] = gateway.catalog(settings.monitoring_team_id)
            message = f'Source dataset {result["dataset_id"]} uploaded to Supervisely.'
        elif name == 'preview':
            pid, preview = setup.preview(gateway, uid, f['dataset_ids'], f['team_ids'], f['replicas'],
                f['batch_units'], f['image_units'], f.get('pilot') is True, f.get('with_annotations') is True)
            data.update(plan_id=pid, preview=preview)
            message = 'Review the allocation below, then apply it to create independent team copies.'
        elif name == 'distribute':
            setup.distribute(gateway, uid, f['plan_id'])
            data.update(plan_id=None, preview=None)
            message = 'Independent team datasets and locked batches are ready. Each monitor can release the first task.'
        elif name == 'inspect':
            setup.acknowledge(uid, key, f['inspection_note'])
            message = 'Inspection recorded. Setup is unblocked; this operation will not be retried.'
        elif name != 'refresh':
            raise WorkflowError('Unknown setup action.')
        data.update(setup=setup.snapshot(), can_setup=True)
        dashboard.display(service.snapshot(uid), message)
        return {'ok': True}
    except WorkflowError as exc:
        # Setup data is only sent after successful organiser authorization.
        if authorized:
            DataJson()['dashboard']['setup'] = setup.snapshot()
        else:
            DataJson()['dashboard'].update(setup={}, catalog={}, can_setup=False, plan_id=None, preview=None)
        dashboard.display(service.snapshot(uid) if uid else {'teams': []}, str(exc), True)
        return {'ok': False}
    except Exception:
        log.warning('Setup request failed; inspect operation history before retrying.')
        if not authorized:
            DataJson()['dashboard'].update(setup={}, catalog={}, can_setup=False, plan_id=None)
        else:
            DataJson()['dashboard']['setup'] = setup.snapshot()
        dashboard.display(service.snapshot(uid) if uid else {'teams': []},
            'Supervisely setup request failed. Check permissions and setup operation history before retrying.', True)
        return {'ok': False}
