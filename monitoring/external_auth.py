"""External monitor sign-in with real email verification and DB-backed sessions.

The Monitors sheet is the allowlist, not proof of email ownership. The event
organiser email may sign in before the workbook has been uploaded.
"""
import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy import select

from .db import (EventRosterRecord, ExternalLoginChallenge,
                 ExternalLoginSession, now)
from .service import WorkflowError

CODE_TTL = 600
SESSION_TTL = 18 * 3600
MIN_SEND_INTERVAL = 60
HOURLY_SEND_LIMIT = 12
MAX_ATTEMPTS = 5


def normal_email(value):
    email = str(value or '').strip().casefold()
    if (len(email) > 254 or email.count('@') != 1 or
            not all(email.split('@', 1)) or any(c.isspace() for c in email)):
        raise WorkflowError('Enter a valid email address.')
    return email


class ExternalAuth:
    def __init__(self, sessions, secret=None):
        self.sessions = sessions
        self.secret = (secret or os.getenv('EXTERNAL_AUTH_SECRET', '')).encode()
        if len(self.secret) < 32:
            raise RuntimeError('EXTERNAL_AUTH_SECRET must contain at least 32 characters.')

    def _allowed(self, session, email):
        owner = os.getenv('EXTERNAL_ORGANISER_EMAIL', '').strip().casefold()
        if not owner:
            raise RuntimeError('EXTERNAL_ORGANISER_EMAIL must be configured.')
        if email == owner:
            return True
        return session.scalar(select(EventRosterRecord.record_id).where(
            EventRosterRecord.source_sheet == 'Monitors',
            EventRosterRecord.email == email,
            EventRosterRecord.event_role.in_(('monitor', 'participant_monitor'))
        ).limit(1)) is not None

    def _digest(self, email, code):
        return hmac.new(self.secret,
                        (email + ':' + code).encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _send(email, code):
        host = os.getenv('SMTP_HOST', '').strip()
        sender = os.getenv('SMTP_FROM', '').strip()
        port = int(os.getenv('SMTP_PORT', '587'))
        if not host or not sender or port not in (465, 587):
            raise RuntimeError('Configure SMTP_HOST, SMTP_FROM and TLS port 465 or 587.')
        msg = EmailMessage()
        msg['To'] = email
        msg['From'] = sender
        msg['Subject'] = 'Bird Annotatiathon — monitoring app sign-in'
        msg.set_content('Your monitoring app code: ' + code +
                        '\\n\\nThis code expires in 10 minutes. '
                        'If you did not request it, ignore this email.')
        username = os.getenv('SMTP_USER', '').strip()
        password = os.getenv('SMTP_PASSWORD', '')
        if bool(username) != bool(password):
            raise RuntimeError('Provide both SMTP_USER and SMTP_PASSWORD or neither.')
        if port == 465:
            connection = smtplib.SMTP_SSL(host, port, timeout=10,
                                           context=ssl.create_default_context())
        else:
            connection = smtplib.SMTP(host, port, timeout=10)
        with connection as client:
            if port == 587:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(msg)

    def request_code(self, raw_email):
        email = normal_email(raw_email)
        moment = now()
        with self.sessions.begin() as session:
            if not self._allowed(session, email):
                return False  # Do not reveal membership in the HTTP response.
            row = session.scalar(select(ExternalLoginChallenge).where(
                ExternalLoginChallenge.email == email).with_for_update())
            if row is None:
                row = ExternalLoginChallenge(
                    email=email, code_digest='', expires_at=0,
                    attempts=0, last_sent_at=0,
                    window_started_at=moment, window_count=0)
                session.add(row)
            if moment - row.last_sent_at < MIN_SEND_INTERVAL:
                return False
            if moment - row.window_started_at >= 3600:
                row.window_started_at, row.window_count = moment, 0
            if row.window_count >= HOURLY_SEND_LIMIT:
                return False
            code = f'{secrets.randbelow(1000000):06d}'
            # SMTP is inside the transaction: a failed send does not consume
            # the challenge or mark a code as delivered.
            self._send(email, code)
            row.code_digest = self._digest(email, code)
            row.expires_at = moment + CODE_TTL
            row.attempts = 0
            row.last_sent_at = moment
            row.window_count += 1
        return True

    def verify_code(self, raw_email, code):
        email = normal_email(raw_email)
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise WorkflowError('The code must have six digits.')
        moment = now()
        with self.sessions.begin() as session:
            if not self._allowed(session, email):
                raise WorkflowError('Invalid or expired verification code.')
            row = session.scalar(select(ExternalLoginChallenge).where(
                ExternalLoginChallenge.email == email).with_for_update())
            if (row is None or row.expires_at <= moment or
                    row.attempts >= MAX_ATTEMPTS):
                raise WorkflowError('Invalid or expired verification code.')
            row.attempts += 1
            if not hmac.compare_digest(row.code_digest, self._digest(email, code)):
                raise WorkflowError('Invalid or expired verification code.')
            row.expires_at = 0
            row.code_digest = ''
            raw_token = secrets.token_urlsafe(32)
            session.add(ExternalLoginSession(
                token_digest=hashlib.sha256(raw_token.encode()).hexdigest(),
                email=email, expires_at=moment + SESSION_TTL))
        return raw_token

    def session_email(self, raw_token):
        if not raw_token or len(raw_token) > 256:
            return None
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        with self.sessions() as session:
            entry = session.get(ExternalLoginSession, digest)
            if not entry or entry.expires_at <= now():
                return None
            return entry.email if self._allowed(session, entry.email) else None

    def logout(self, raw_token):
        if not raw_token:
            return
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        with self.sessions.begin() as session:
            row = session.get(ExternalLoginSession, digest)
            if row:
                session.delete(row)
