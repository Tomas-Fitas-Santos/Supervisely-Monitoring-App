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
    settings = Settings.load()
    # Import once so UI onboarding and the worker use the same live event configuration.
    from . import ui
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
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
        uvicorn.run(ui.app, host='127.0.0.1' if settings.local else '0.0.0.0', port=8000)
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
