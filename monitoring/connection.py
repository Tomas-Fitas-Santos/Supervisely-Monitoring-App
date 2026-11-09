"""Supervisely browser logins and the organiser's encrypted polling credential."""
import os
import hashlib
import secrets
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update

from .db import EventConfig, EventMember, EventPreferences, LoginSession, SetupState, Team, now
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

    def save(self, server, token, legacy_monitoring_team_id=None, expected_user_id=None, browser=False):
        """Authenticate the supplied user ID with that user's personal token.

        Browser logins have independent credentials. Only the organiser's
        credential may be retained for background event polling.
        """
        import supervisely as sly

        server = server_url(server)
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise WorkflowError('Enter your Supervisely API token to connect.')
        if expected_user_id is not None:
            try:
                parsed_uid = int(expected_user_id)
            except (TypeError, ValueError):
                raise WorkflowError('Enter a positive Supervisely User ID.') from None
            if parsed_uid <= 0 or isinstance(expected_user_id, bool):
                raise WorkflowError('Enter a positive Supervisely User ID.')
        else:
            parsed_uid = None  # Compatibility with existing authenticated tests.
        api = sly.Api(server, token.strip())
        try:
            user = api.user.get_my_info()
        except Exception:
            raise WorkflowError(
                'Could not authenticate. Check the Supervisely server and API token.'
            ) from None
        if parsed_uid is not None and int(user.id) != parsed_uid:
            raise WorkflowError(
                'The token belongs to another Supervisely user. Enter the User ID '
                'associated with this personal API token.'
            )

        with self.sessions() as session:
            old = session.get(EventConfig, 1)
            if old.server_address and old.server_address.rstrip('/') != server:
                raise WorkflowError('This event is bound to another Supervisely server.')
            if old.owner_id is not None and int(old.owner_id) != int(user.id):
                if not old.monitoring_team_id:
                    raise WorkflowError(
                        'The event organiser must configure the shared team before '
                        'other monitors can connect.'
                    )
                try:
                    member = api.user.get_member_info_by_id(
                        old.monitoring_team_id, int(user.id))
                except Exception:
                    raise WorkflowError(
                        'The account is not an accepted member of the configured event team.'
                    ) from None
                registered = session.get(EventMember, int(user.id))
                if (not member or getattr(member, 'disabled', False)
                        or not registered or registered.group != 'monitors'
                        or getattr(member, 'role', '') not in ('admin', 'manager', 'developer', 'reviewer')):
                    raise WorkflowError(
                        'This account is not approved as a monitor for the event.'
                    )
            populated = session.scalar(select(Team.id).limit(1)) is not None
            if populated and old.owner_id is None:
                if not legacy_monitoring_team_id:
                    raise WorkflowError(
                        'An existing event needs its original organiser connection.'
                    )
                from .setup_gateway import SetupGateway
                SetupGateway(api).organiser(legacy_monitoring_team_id, user.id)

            if browser and old.owner_id is not None and old.owner_id != int(user.id):
                # Signing in as a monitor does not change shared event settings
                # and need not wait for an organiser's upload operation.
                return api, user

        encrypted = self.cipher().encrypt(token.strip().encode()).decode()
        with self.sessions.begin() as session:
            lock = session.execute(update(SetupState).where(
                SetupState.id == 1,
                SetupState.operation_id.is_(None),
            ).values(operation_id='connection'))
            if lock.rowcount != 1:
                raise WorkflowError(
                    'Finish or inspect the current setup operation before reconnecting.'
                )
            config = session.get(EventConfig, 1)
            if config.server_address and config.server_address.rstrip('/') != server:
                raise WorkflowError('The event server changed while connecting.')
            if config.owner_id is None:
                config.owner_id, config.owner_login = int(user.id), user.login
            config.server_address = server
            if config.owner_id == int(user.id):
                config.local_token = encrypted
            prefs = session.get(EventPreferences, 1)
            if not browser:
                prefs.active_local_user_id = int(user.id)
                prefs.active_local_login = user.login
            session.execute(update(SetupState).where(
                SetupState.id == 1,
                SetupState.operation_id == 'connection',
            ).values(operation_id=None))
        return api, user

    def login(self, server, token, expected_user_id, legacy_monitoring_team_id=None):
        api, user = self.save(server, token, legacy_monitoring_team_id,
                              expected_user_id, browser=True)
        secret = secrets.token_urlsafe(32)
        with self.sessions.begin() as session:
            session.add(LoginSession(id=self.session_id(secret), user_id=int(user.id),
                login=user.login, server=api.server_address.rstrip('/'),
                token=self.cipher().encrypt(token.strip().encode()).decode(),
                expires_at=now() + 12 * 60 * 60))
        return secret, user

    @staticmethod
    def session_id(secret):
        return hashlib.sha256(str(secret).encode()).hexdigest()

    def session(self, secret):
        if not secret:
            return None
        with self.sessions() as session:
            row = session.get(LoginSession, self.session_id(secret))
            return row if row and row.expires_at > now() else None

    def session_api(self, secret):
        row = self.session(secret)
        if not row:
            raise WorkflowError('Sign in with your own Supervisely account to continue.')
        try:
            token = self.cipher().decrypt(row.token.encode()).decode()
        except InvalidToken:
            raise WorkflowError('Your saved login is unavailable. Sign in again.') from None
        import supervisely as sly
        return sly.Api(row.server, token), row.user_id

    def logout(self, secret):
        with self.sessions.begin() as session:
            row = session.get(LoginSession, self.session_id(secret))
            if row:
                session.delete(row)
