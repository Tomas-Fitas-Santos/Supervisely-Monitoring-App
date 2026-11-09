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
    parser = argparse.ArgumentParser(description='Run the Supervisely Monitoring App.')
    hosting = parser.add_mutually_exclusive_group()
    hosting.add_argument('--local', action='store_true', help='Run locally and sign in with Supervisely.')
    hosting.add_argument('--hosted', action='store_true', help='Run with Supervisely multi-user authentication and PostgreSQL.')
    hosting.add_argument('--production', dest='hosted', action='store_true', help=argparse.SUPPRESS)
    hosting.add_argument('--external', action='store_true', help='Run external HTTPS monitoring with email-code login.')
    args = parser.parse_args()
    if args.hosted:
        from .deployment import production_environment
        production_environment()
    if args.local:
        from .launch import configure_local_environment
        configure_local_environment()
    if args.external:
        from .deployment import external_environment
        external_environment()
    settings = Settings.load()
    # Import once so UI onboarding and the worker use the same live event configuration.
    from . import ui
    if not settings.local:
        from .deployment import production_database
        production_database(ui.sessions)
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                token = os.getenv('SYNC_API_TOKEN') or (os.getenv('SUPERVISELY_SERVICE_TOKEN') if args.external else None)
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
        if args.external:
            from .external_server import ExternalShell
            application = ExternalShell(ui.app, ui.dashboard, ui.sessions)
        else:
            application = LocalShell(ui.app, ui.dashboard,
                lambda secret: ui.connection.session(secret)) if settings.local else ui.app
        uvicorn.run(application, host='127.0.0.1' if settings.local else '0.0.0.0', port=8000)
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
