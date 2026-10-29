"""Select local stores before importing the application or constructing a gateway."""
import os
from pathlib import Path


def configure_local_mode(mode):
    if mode not in ('demo', 'pilot', 'simulator'):
        raise ValueError('Unknown local launch mode.')
    if any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
        raise RuntimeError('Local demo/pilot/simulator flags cannot run inside a hosted Supervisely session.')
    simulator = mode == 'simulator'
    # Demo and Pilot are the same live workflow, including saved credentials,
    # IDs and recovery checkpoints. Switching names must not provision twice.
    home = Path('data/organiser-demo' if simulator else 'data/account-pilot').resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        LOCAL_DEVELOPMENT='true', LOCAL_APP_HOME=str(home),
        DATABASE_URL='sqlite:///' + (home / ('demo.db' if simulator else 'pilot.db')).as_posix(),
        MONITORING_SIMULATOR='1' if simulator else '0',
        MONITORING_LIVE_DEMO='1' if mode == 'demo' else '0',
        MONITORING_PILOT='0' if simulator else '1',
        # Do not let an old .env select the former offline Demo workflow.
        MONITORING_DEMO='0', MONITORING_TEAM_ID='0',
        UPLOAD_STAGING_DIR=str(home / 'uploads'),
        API_TOKEN='', SYNC_API_TOKEN='', LOCAL_USER_ID='',
    )
    return home
