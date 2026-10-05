import logging
import os
import json
import threading
from dataclasses import replace
from pathlib import Path

import supervisely as sly
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select, update

from . import controller
from .dashboard import Dashboard
from .db import Batch, EventConfig, EventMember, Review, database, initialize
from .gateway import Gateway
from .service import Service, WorkflowError
from .settings import Settings
from .setup import Setup
from .setup_gateway import SetupGateway
from .connection import LocalConnection

sly.env.enable_multiuser_app_mode()
settings = Settings.load()
engine, sessions = database(settings.database_url)
initialize(engine)
service = Service(sessions, settings.stale_after)
setup = Setup(sessions, settings.monitoring_team_id)
connection = LocalConnection(sessions, settings.home)
connection_lock = threading.Lock()
dashboard = Dashboard()
app = sly.Application(layout=dashboard, static_dir=str(Path(__file__).parent / 'static'))
log = logging.getLogger(__name__)


def event_config():
    with sessions() as s:
        return s.get(EventConfig, 1)


def identity(request):
    config = event_config()
    if settings.local:
        if request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise WorkflowError('Local development is only available on loopback.')
        api = connection.api()
        if api is None and config.owner_id is None and os.getenv('API_TOKEN'):
            api = sly.Api.from_env()  # Compatibility with existing configured pilots.
        if api is None:
            if config.owner_id is None and os.getenv('LOCAL_USER_ID'):
                return int(os.environ['LOCAL_USER_ID']), None
            raise WorkflowError('Connect your Supervisely account to start the event.')
    else:
        api = getattr(request.state, 'api', None)
        uid = sly.env.user_from_multiuser_app()
        if api is None or uid is None:
            raise WorkflowError('Open this app through your Supervisely monitoring team.')
    expected_server = config.server_address or settings.server_address
    if api.server_address.rstrip('/') != expected_server:
        raise WorkflowError('The session belongs to a different Supervisely server.')
    profile = api.user.get_my_info()
    if settings.local:
        uid = int(profile.id)
    elif int(profile.id) != int(uid):
        raise WorkflowError('Session identity and API credential do not match.')
    uid = int(uid)
    gateway = Gateway(api)
    if config.monitoring_team_id:
        gateway.member(config.monitoring_team_id, uid)
        member = api.user.get_member_info_by_id(config.monitoring_team_id, uid)
        with sessions() as s:
            roster_member = s.get(EventMember, uid)
            if roster_member and roster_member.group == 'annotators':
                raise WorkflowError("Annotators use the Supervisely web app. Only the Monitors group can use this app.")
            if (uid != config.owner_id and member.role != 'admin'
                    and (not roster_member or roster_member.group != 'monitors')):
                raise WorkflowError("You are not in this event's Monitors group.")
    elif config.owner_id is not None:
        if config.owner_id != uid:
            raise WorkflowError('Only the organiser can finish the event groups.')
    elif setup.monitoring_team_id:
        gateway.member(setup.monitoring_team_id, uid)
    elif not settings.local:
        # Fresh hosted onboarding is restricted to an Admin of the app's launch team.
        launch_id = settings.launch_team_id or sly.env.team_id(raise_not_found=False)
        if not launch_id:
            raise WorkflowError('Launch the app from a Supervisely team where you are an Admin.')
        SetupGateway(api).organiser(launch_id, uid)
        with sessions.begin() as s:
            claimed = s.execute(update(EventConfig).where(EventConfig.id == 1, EventConfig.owner_id.is_(None))
                .values(owner_id=uid, owner_login=api.user.get_my_info().login,
                        server_address=api.server_address.rstrip('/')))
            if claimed.rowcount != 1 and s.get(EventConfig, 1).owner_id != uid:
                raise WorkflowError('Another organiser started this event first.')
    else:
        raise WorkflowError('Connect through the app to initialize this event.')
    return uid, gateway


def connection_view(connected=False):
    config = event_config()
    return {'local': settings.local, 'connected': connected, 'server_address': config.server_address or settings.server_address,
            'login': config.owner_login or '', 'groups_ready': bool(config.monitoring_team_id and config.annotator_team_id)}


def display_access(uid, gateway):
    from supervisely.app import DataJson
    can_setup = False
    if gateway:
        try:
            setup.authorize(SetupGateway(gateway.api), uid)
            can_setup = True
        except WorkflowError:
            pass
    data = DataJson()['dashboard']
    data.update(can_setup=can_setup, connection=connection_view(gateway is not None))
    if can_setup:
        data['setup'] = setup.snapshot()
    else:
        data.update(setup={}, catalog={}, plan_id=None, preview=None,
                    assignment_plan_id=None, assignment_preview=[])
    return can_setup


def clear_access():
    from supervisely.app import DataJson
    DataJson()['dashboard'].update(setup={}, catalog={}, can_setup=False, plan_id=None, preview=None,
                                   assignment_plan_id=None, assignment_preview=[],
                                   connection=connection_view(False))


def local_connect(values):
    global settings, engine, sessions, service, setup, connection
    with connection_lock:
        with sessions() as s:
            demo = s.scalar(select(Batch).where(Batch.id.like('demo-%')).limit(1))
            synthetic = demo and any(a['source_id'].startswith('demo-') for a in demo.assets)
        if synthetic:
            # Start a separate live database; never delete or mutate the user's synthetic demo.
            live_path = settings.home / 'event.db'
            if settings.database_url == 'sqlite:///' + live_path.as_posix():
                live_path = settings.home / 'live-event.db'
            url = 'sqlite:///' + live_path.as_posix()
            new_engine, new_sessions = database(url)
            initialize(new_engine)
            candidate = LocalConnection(new_sessions, settings.home)
        else:
            candidate = connection
            url, new_engine, new_sessions = settings.database_url, engine, sessions
        api, user = candidate.save(values.get('server'), values.get('token'),
                                   settings.monitoring_team_id if not synthetic else None)
        if synthetic:
            settings = replace(settings, database_url=url, monitoring_team_id=0)
            engine, sessions = new_engine, new_sessions
            service = Service(sessions, settings.stale_after)
            setup = Setup(sessions, 0)
            connection = candidate
            preferences = settings.home / 'settings.json'
            temp = settings.home / 'settings.pending'
            temp.write_text(json.dumps({'database_url': url}))
            temp.replace(preferences)
        return {'ok': True, 'login': user.login}


@app.get_server().post('/connection')
async def connect(request: Request):
    if not settings.local or request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        return JSONResponse({'ok': False, 'message': 'Use your Supervisely session to authenticate.'}, status_code=403)
    # This endpoint does not use widget state; credentials cannot be echoed back by the SDK.
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        return JSONResponse({'ok': False, 'message': 'Open the connection screen on this app.'}, status_code=403)
    try:
        values = await request.json()
        return await run_in_threadpool(local_connect, values)
    except WorkflowError as exc:
        return JSONResponse({'ok': False, 'message': str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({'ok': False, 'message': 'Could not save the connection. Check your server and token.'}, status_code=400)


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
        display_access(uid, gateway)
        dashboard.display(service.snapshot(uid), message, links=links)
    except WorkflowError as e:
        # Authentication failures never retain another user's cached dashboard.
        if uid is None:
            clear_access()
        dashboard.display(service.snapshot(uid) if uid is not None else {'teams': []}, str(e),
                          not (uid is None and str(e).startswith('Connect your Supervisely')))
    except Exception:
        log.warning('Dashboard action failed; user must refresh or reconcile any pending release.')
        if uid is None:
            clear_access()
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
            data['catalog'] = gateway.catalog(setup.monitoring_team_id)
            message = 'Live Supervisely users and datasets loaded.'
        elif name == 'groups':
            existing_monitor = f.get('existing_monitor_group')
            if not settings.local:
                existing_monitor = settings.launch_team_id or sly.env.team_id(raise_not_found=False)
            setup.configure_groups(gateway, uid, key, f['monitor_group_name'], f['annotator_group_name'],
                                   existing_monitor, f.get('existing_annotator_group'))
            data['catalog'] = gateway.catalog(setup.monitoring_team_id)
            message = 'Monitors and Annotators groups are ready. Add registered people to each group.'
        elif name == 'members':
            setup.add_members(gateway, uid, key, f['member_group'], f['member_logins'])
            data['catalog'] = gateway.catalog(setup.monitoring_team_id)
            message = 'People added to the event group.'
        elif name == 'member_remove':
            setup.remove_member(gateway, uid, key, f['member_user_id'])
            message = 'Person removed from the event roster.'
        elif name == 'balance_preview':
            pid, rows = setup.preview_monitor_assignments(gateway, uid, f['monitor_ids'])
            data.update(assignment_plan_id=pid, assignment_preview=rows)
            message = 'Review the proposed team-to-monitor assignments.'
        elif name == 'balance_apply':
            setup.apply_monitor_assignments(gateway, uid, f['assignment_plan_id'])
            data.update(assignment_plan_id=None, assignment_preview=[])
            message = 'Participant pairs assigned to their monitors.'
        elif name == 'team':
            tid = setup.create_team(gateway, uid, key, f['name'], f['logins'], f['monitor_id'], f.get('existing_team_id'))
            message = f'Participant pair {tid} registered. Assign a monitor below if you chose Assign later.'
        elif name == 'assign':
            setup.assign_monitor(gateway, uid, key, f['setup_team_id'], f['monitor_id'], f['setup_revision'])
            message = 'Monitor assignment saved. The previous monitor no longer has app access to this team.'
        elif name == 'chunk':
            data['upload_offset'] = setup.stage(uid, f['upload_id'], f['filename'], f['size'], f['offset'], f['chunk'])
        elif name == 'upload':
            result = setup.upload(gateway, uid, key, f['upload_ids'], f['workspace_id'], f['name'], f['kind'], f.get('meta'))
            data['catalog'] = gateway.catalog(setup.monitoring_team_id)
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
        data.update(setup=setup.snapshot(), can_setup=True, connection=connection_view(True))
        dashboard.display(service.snapshot(uid), message)
        return {'ok': True}
    except WorkflowError as exc:
        # Setup data is only sent after successful organiser authorization.
        if authorized:
            DataJson()['dashboard']['setup'] = setup.snapshot()
        else:
            clear_access()
        dashboard.display(service.snapshot(uid) if uid else {'teams': []}, str(exc), True)
        return {'ok': False}
    except Exception:
        log.warning('Setup request failed; inspect operation history before retrying.')
        if not authorized:
            clear_access()
        else:
            DataJson()['dashboard']['setup'] = setup.snapshot()
        dashboard.display(service.snapshot(uid) if uid else {'teams': []},
            'Supervisely setup request failed. Check permissions and setup operation history before retrying.', True)
        return {'ok': False}
