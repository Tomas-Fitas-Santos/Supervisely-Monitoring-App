import os
import json
from pathlib import Path
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
    home: Path = Path("data")
    launch_team_id: int | None = None

    @classmethod
    def load(cls):
        load_dotenv()
        hosted = any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID'))
        local = os.getenv('LOCAL_DEVELOPMENT', 'false' if hosted else 'true').lower() == 'true'
        if local and any(os.getenv(k) for k in ('TASK_ID', 'task_id', 'SLY_APP_TASK_ID')):
            raise RuntimeError('Local development identity is forbidden in a Supervisely app session.')
        home = Path(os.getenv('LOCAL_APP_HOME', 'data')).resolve()
        preferences = {}
        if local:
            home.mkdir(parents=True, exist_ok=True)
            if (home / 'settings.json').exists():
                preferences = json.loads((home / 'settings.json').read_text())
        url = preferences.get('database_url') or os.getenv('DATABASE_URL')
        if not url:
            if not local:
                raise RuntimeError('Configure persistent PostgreSQL storage for the hosted session.')
            url = 'sqlite:///' + (home / 'event.db').as_posix()
        if not local and not url.startswith('postgresql+psycopg://'):
            raise RuntimeError('Hosted multi-user apps require persistent PostgreSQL storage.')
        interval = int(os.getenv('POLL_INTERVAL_SECONDS', '60'))
        stale = int(os.getenv('STALE_AFTER_SECONDS', '180'))
        if interval < 15 or stale < interval:
            raise RuntimeError('Use polling >=15 seconds and a stale threshold >= polling interval.')
        try:
            legacy_team = int(os.getenv('MONITORING_TEAM_ID') or 0)
        except ValueError:
            legacy_team = 0
        return cls(url, legacy_team,
                   os.getenv('SERVER_ADDRESS', 'https://app.supervisely.com').rstrip('/'), interval, stale, local, home,
                   int(os.getenv('CONTEXT_TEAMID') or os.getenv('TEAM_ID') or 0) or None)
