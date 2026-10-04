import logging
import os
from pathlib import Path

import supervisely as sly
from fastapi import Request
from supervisely.app import StateJson

from . import controller
from .dashboard import Dashboard
from .db import Review, database, initialize
from .gateway import Gateway
from .service import Service, WorkflowError
from .settings import Settings

sly.env.enable_multiuser_app_mode()
settings = Settings.load()
engine, sessions = database(settings.database_url)
initialize(engine)
service = Service(sessions, settings.stale_after)
dashboard = Dashboard()
app = sly.Application(layout=dashboard, static_dir=str(Path(__file__).parent / 'static'))
log = logging.getLogger(__name__)


def identity(request):
    if settings.local:
        # Loopback smoke test only; the runner binds localhost in this mode.
        if request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            raise WorkflowError('Local development is only available on loopback.')
        uid = int(os.environ['LOCAL_USER_ID'])
        api = sly.Api.from_env() if os.getenv('API_TOKEN') else None
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


@app.get_server().post('/dashboard/action')
def action(request: Request):
    uid = None
    try:
        uid, gateway = identity(request)
        f = dict(StateJson()['dashboard'])
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
        dashboard.display(service.snapshot(uid), message, links=links)
    except WorkflowError as e:
        # Authentication failures never retain another user's cached dashboard.
        dashboard.display(service.snapshot(uid) if uid is not None else {'teams': []}, str(e), True)
    except Exception:
        log.warning('Dashboard action failed; user must refresh or reconcile any pending release.')
        dashboard.display(service.snapshot(uid) if uid is not None else {'teams': []},
                          'Action failed. Refresh and check the task status before retrying.', True)
