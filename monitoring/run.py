import logging
import os
import threading

import supervisely as sly
import uvicorn

from .gateway import Gateway
from .settings import Settings
from .worker import poll_once

log = logging.getLogger(__name__)


def main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Run the shared monitoring app or isolated organiser demo.')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--demo', action='store_true', help='Offline simulator with bundled CC0 photos; no actual Supervisely operations.')
    modes.add_argument('--pilot', action='store_true', help='Real-account pilot with a separate local database and bundled roster/photos. Connect in the app.')
    modes.add_argument('--production', action='store_true', help='Require a native authenticated multi-user Supervisely session and persistent PostgreSQL.')
    args = parser.parse_args()
    if args.production:
        from .deployment import production_environment
        production_environment()
    if args.demo or args.pilot:
        if any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
            raise RuntimeError('Local demo/pilot launch flags cannot run inside a hosted Supervisely session.')
        home = Path('data/organiser-demo' if args.demo else 'data/account-pilot').resolve()
        home.mkdir(parents=True, exist_ok=True)
        os.environ.update(LOCAL_DEVELOPMENT='true', LOCAL_APP_HOME=str(home),
                          DATABASE_URL='sqlite:///' + (home / ('demo.db' if args.demo else 'pilot.db')).as_posix(),
                          MONITORING_DEMO='1' if args.demo else '0', MONITORING_PILOT='1' if args.pilot else '0',
                          MONITORING_TEAM_ID='0', UPLOAD_STAGING_DIR=str(home / 'uploads'),
                          API_TOKEN='', SYNC_API_TOKEN='', LOCAL_USER_ID='')
    settings = Settings.load()
    # Import once so UI onboarding and the worker use the same live event configuration.
    from . import ui
    if not settings.local:
        from .deployment import production_database
        production_database(ui.sessions)
    if args.demo:
        from .demo import initialize_demo
        initialize_demo(ui.sessions)
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                if args.demo:
                    stop.wait(settings.interval)
                    continue
                token = os.getenv('SYNC_API_TOKEN')
                server = ui.event_config().server_address or ui.settings.server_address
                api = sly.Api(server, token) if token else (
                    ui.connection.api() if ui.settings.local else None)
                if api and (ui.setup.groups()['ready'] or (ui.setup.monitoring_team_id and ui.setup.roster())):
                    poll_once(ui.service, Gateway(api), settings.interval)
            except Exception:
                log.warning('Polling cycle failed; reconnect or refresh Supervisely in the app.')
            stop.wait(settings.interval)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        from .local_shell import LocalShell
        application = LocalShell(ui.app, ui.dashboard) if settings.local else ui.app
        uvicorn.run(application, host='127.0.0.1' if settings.local else '0.0.0.0', port=8000)
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
