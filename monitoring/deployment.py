"""Fail-closed production startup, separate from local rehearsal modes."""
import os

from .service import WorkflowError


def production_environment():
    if not any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
        raise WorkflowError('Production must run inside a native Supervisely application session. Use --pilot for a local real-account rehearsal.')
    if os.getenv('LOCAL_DEVELOPMENT', '').lower() == 'true':
        raise WorkflowError('Production cannot enable LOCAL_DEVELOPMENT.')
    if any(os.getenv(k) == '1' for k in ('MONITORING_DEMO', 'MONITORING_PILOT')):
        raise WorkflowError('Production cannot enable simulator or local pilot controls.')
    os.environ['LOCAL_DEVELOPMENT'] = 'false'


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
