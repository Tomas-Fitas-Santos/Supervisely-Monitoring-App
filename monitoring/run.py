import os
import threading

import supervisely as sly
import uvicorn

from .db import database, initialize
from .gateway import Gateway
from .service import Service
from .settings import Settings
from .worker import poll_forever


def main():
    settings = Settings.load()
    stop = threading.Event()
    token = os.getenv('SYNC_API_TOKEN')
    thread = None
    if token:
        engine, sessions = database(settings.database_url)
        initialize(engine)
        gateway = Gateway(sly.Api(settings.server_address, token))
        thread = threading.Thread(target=poll_forever,
            args=(Service(sessions, settings.stale_after), gateway, stop, settings.interval), daemon=True)
        thread.start()
    try:
        uvicorn.run('monitoring.ui:app', host='127.0.0.1' if settings.local else '0.0.0.0', port=8000)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
