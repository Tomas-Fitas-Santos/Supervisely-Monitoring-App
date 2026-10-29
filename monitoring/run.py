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
    parser = argparse.ArgumentParser(description='Run the monitoring app, live sample demo or offline simulator.')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--demo', action='store_true', help='Live Supervisely demo with the sample accounts and bird photos. Connect in the app; creates real teams, invitations, uploads and tasks.')
    modes.add_argument('--pilot', action='store_true', help='Alias for the live demo workflow and saved database. Connect in the app.')
    modes.add_argument('--simulator', action='store_true', help='Offline development simulator only; creates no real Supervisely resources and sends no invitations.')
    modes.add_argument('--production', action='store_true', help='Require a native authenticated multi-user Supervisely session and persistent PostgreSQL.')
    args = parser.parse_args()
    if args.production:
        from .deployment import production_environment
        production_environment()
    if args.demo or args.pilot or args.simulator:
        from .launch import configure_local_mode
        configure_local_mode('simulator' if args.simulator else 'demo' if args.demo else 'pilot')
    settings = Settings.load()
    # Import once so UI onboarding and the worker use the same live event configuration.
    from . import ui
    if not settings.local:
        from .deployment import production_database
        production_database(ui.sessions)
    if args.simulator:
        from .demo import initialize_demo
        initialize_demo(ui.sessions)
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                if args.simulator:
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
