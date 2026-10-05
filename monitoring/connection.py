"""One-time local connection. Plaintext credentials never enter widget state or exports."""
import os
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update

from .db import EventConfig, SetupState, Team
from .service import WorkflowError


def server_url(value):
    value = str(value).strip().rstrip('/')
    parsed = urlparse(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise WorkflowError('Enter the base address of your Supervisely server.')
    return value


class LocalConnection:
    def __init__(self, sessions, home):
        self.sessions = sessions
        self.home = Path(home)

    def cipher(self):
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / 'credential.key'
        if not path.exists():
            # Exclusive creation prevents two connection requests from creating different keys.
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as f:
                    f.write(Fernet.generate_key())
                    f.flush()
                    os.fsync(f.fileno())
        try:
            return Fernet(path.read_bytes())
        except (ValueError, OSError):
            raise WorkflowError('Local connection key is unavailable. Restore the key before reconnecting.') from None

    def api(self):
        with self.sessions() as s:
            config = s.get(EventConfig, 1)
            if not config or not config.local_token:
                return None
            try:
                token = self.cipher().decrypt(config.local_token.encode()).decode()
            except InvalidToken:
                raise WorkflowError('The saved connection cannot be decrypted. Reconnect through the app.') from None
            import supervisely as sly
            return sly.Api(config.server_address, token)

    def save(self, server, token, legacy_monitoring_team_id=None):
        import supervisely as sly
        server = server_url(server)
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise WorkflowError('Enter your Supervisely API token to connect.')
        api = sly.Api(server, token.strip())
        try:
            user = api.user.get_my_info()
        except Exception:
            raise WorkflowError('Could not authenticate. Check the server address and API token.') from None
        with self.sessions() as s:
            old = s.get(EventConfig, 1)
            populated = s.scalar(select(Team.id).limit(1)) is not None
            if old.owner_id is not None and (old.owner_id != user.id or old.server_address != server):
                raise WorkflowError('Reconnect with the organiser account and server that own this event.')
            if populated and old.owner_id is None:
                if not legacy_monitoring_team_id:
                    raise WorkflowError('An existing event needs its original organiser connection.')
                from .setup_gateway import SetupGateway
                SetupGateway(api).organiser(legacy_monitoring_team_id, user.id)
        encrypted = self.cipher().encrypt(token.strip().encode()).decode()
        with self.sessions.begin() as s:
            # Serialize with other setup writes and the first organiser connection.
            lock = s.execute(update(SetupState).where(SetupState.id == 1,
                SetupState.operation_id.is_(None)).values(operation_id='connection'))
            if lock.rowcount != 1:
                raise WorkflowError('Finish or inspect the current setup operation before reconnecting.')
            config = s.get(EventConfig, 1)
            if config.owner_id is not None and (config.owner_id != user.id or config.server_address != server):
                raise WorkflowError('Another organiser connected this event first.')
            config.owner_id, config.owner_login = user.id, user.login
            config.server_address, config.local_token = server, encrypted
            s.execute(update(SetupState).where(SetupState.id == 1,
                SetupState.operation_id == 'connection').values(operation_id=None))
        return api, user
