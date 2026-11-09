"""Fail-closed production startup, separate from local rehearsal modes."""
import os

from .service import WorkflowError


def production_environment():
    if not any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
        raise WorkflowError('Hosted deployment requires a native Supervisely application session. Use --local to run the app locally.')
    if os.getenv('LOCAL_DEVELOPMENT', '').lower() == 'true':
        raise WorkflowError('Production cannot enable LOCAL_DEVELOPMENT.')
    if any(os.getenv(k) == '1' for k in ('MONITORING_DEMO', 'MONITORING_SIMULATOR', 'MONITORING_PILOT', 'MONITORING_LIVE_DEMO')):
        raise WorkflowError('Production cannot enable simulator or local pilot controls.')
    os.environ['LOCAL_DEVELOPMENT'] = 'false'


def external_environment():
    if any(os.getenv(key) for key in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
        raise WorkflowError('External deployment must not run inside a native Supervisely app session.')
    if any(os.getenv(k) == '1' for k in ('MONITORING_DEMO', 'MONITORING_SIMULATOR',
            'MONITORING_PILOT', 'MONITORING_LIVE_DEMO')):
        raise WorkflowError('External hosting cannot use local DEMO or simulator controls.')
    if not os.getenv('SUPERVISELY_SERVICE_TOKEN'):
        raise WorkflowError('SUPERVISELY_SERVICE_TOKEN is required for external hosting.')
    if not os.getenv('EXTERNAL_ORGANISER_EMAIL') or not os.getenv('EXTERNAL_TEAM_ID'):
        raise WorkflowError('Configure EXTERNAL_ORGANISER_EMAIL and EXTERNAL_TEAM_ID.')
    os.environ['LOCAL_DEVELOPMENT'] = 'false'
    os.environ['MONITORING_EXTERNAL'] = '1'


def production_database(sessions):
    from .db import EventConfig, Simulation
    with sessions() as session:
        if session.bind.dialect.name != 'postgresql':
            raise WorkflowError('Production requires persistent PostgreSQL.')
        if session.get(Simulation, 1):
            raise WorkflowError('Production cannot use a simulator database.')
        config = session.get(EventConfig, 1)
        if config and config.local_token:
            raise WorkflowError('Use a separate production database without a saved local organiser token.')
