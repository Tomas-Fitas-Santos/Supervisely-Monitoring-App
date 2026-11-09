"""Configure local hosting for the one event application."""
import os
from pathlib import Path


def configure_local_environment():
    if any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
        raise RuntimeError('Local hosting cannot replace a hosted Supervisely session.')
    # Retain the established live store so existing connections and events resume.
    home = Path(os.getenv('LOCAL_APP_HOME') or 'data/account-pilot').resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        LOCAL_DEVELOPMENT='true', LOCAL_APP_HOME=str(home),
        DATABASE_URL=os.getenv('DATABASE_URL') or 'sqlite:///' + (home / 'pilot.db').as_posix(),
        UPLOAD_STAGING_DIR=os.getenv('UPLOAD_STAGING_DIR') or str(home / 'uploads'),
        MONITORING_SIMULATOR='0', MONITORING_LIVE_DEMO='0',
        MONITORING_PILOT='0', MONITORING_DEMO='0',
    )
    return home
