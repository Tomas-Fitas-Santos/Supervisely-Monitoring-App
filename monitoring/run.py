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
    parser.add_argument('--demo', action='store_true', help='Loopback-only demo with bundled CC0 photos and simulated accounts.')
    args = parser.parse_args()
    if args.demo:
        if any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
            raise RuntimeError('The organiser simulator cannot run inside a hosted Supervisely session.')
        home = Path('data/organiser-demo').resolve()
        home.mkdir(parents=True, exist_ok=True)
        os.environ.update(LOCAL_DEVELOPMENT='true', LOCAL_APP_HOME=str(home),
                          DATABASE_URL='sqlite:///' + (home / 'demo.db').as_posix(), MONITORING_DEMO='1')
    settings = Settings.load()
    # Import once so UI onboarding and the worker use the same live event configuration.
    from . import ui
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
