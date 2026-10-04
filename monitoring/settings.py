import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    monitoring_team_id: int
    server_address: str
    interval: int
    stale_after: int
    local: bool

    @classmethod
    def load(cls):
        load_dotenv()
        local = os.getenv('LOCAL_DEVELOPMENT', 'false').lower() == 'true'
        if local and any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
            raise RuntimeError('Local development identity is forbidden in a Supervisely app session.')
        url = os.environ['DATABASE_URL']
        if not local and not url.startswith('postgresql+psycopg://'):
            raise RuntimeError('Hosted multi-user apps require persistent PostgreSQL storage.')
        interval = int(os.getenv('POLL_INTERVAL_SECONDS', '60'))
        stale = int(os.getenv('STALE_AFTER_SECONDS', '180'))
        if interval < 15 or stale < interval:
            raise RuntimeError('Use polling >=15 seconds and a stale threshold >= polling interval.')
        return cls(url, int(os.environ['MONITORING_TEAM_ID']),
                   os.getenv('SERVER_ADDRESS', 'https://app.supervisely.com').rstrip('/'), interval, stale, local)
