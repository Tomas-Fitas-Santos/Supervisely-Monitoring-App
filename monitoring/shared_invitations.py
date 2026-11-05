"""A single real email-invitation workflow for DEMO and PRODUCTION.

The Supervisely team must already exist. Only the configured event Team ID is
used, and acceptance is verified against the native active-member listing.
"""
from sqlalchemy import select

from .db import Audit, EventConfig, EventPreferences, Registration, SharedInvitation, now
from .event_settings import single_team
from .service import WorkflowError
from .setup_gateway import InvitationRejected, SetupGateway


def normal_email(email):
    email = str(email or '').strip().casefold()
    if (len(email) > 254 or email.count('@') != 1 or
            not all(email.split('@', 1)) or any(c.isspace() for c in email)):
        raise WorkflowError('The registration contains an invalid email address.')
    return email


class SharedTeamInvitations:
    def __init__(self, sessions):
        self.sessions = sessions

    def _event_team(self, session):
        config = session.get(EventConfig, 1)
        if not single_team(config):
            raise WorkflowError(
                'Open Settings and configure the existing shared Supervisely Team ID.'
            )
        return int(config.monitoring_team_id)

    def registrations(self):
        with self.sessions() as session:
            return [
                (reg.id, reg.team_name, [normal_email(p.get('email'))
                    for p in reg.members if p.get('email')])
                for reg in session.scalars(select(Registration).order_by(Registration.team_name))
            ]

    def _email_to_rows(self):
        data = {}
        for reg_id, team_name, emails in self.registrations():
            if len(emails) != 2 or len(set(emails)) != 2:
                raise WorkflowError(
                    f'Competition team {team_name} requires two distinct participant emails.'
                )
            for address in emails:
                if address in data and data[address] != reg_id:
                    raise WorkflowError('Participant email is registered to multiple pairs.')
                data[address] = reg_id
        return data

    def summary(self):
        with self.sessions() as session:
            team_id = self._event_team(session)
            records = {v.email: v for v in session.scalars(
                select(SharedInvitation).where(SharedInvitation.team_id == team_id)
            )}
            result = {}
            for reg in session.scalars(select(Registration)):
                emails = [normal_email(p.get('email')) for p in reg.members if p.get('email')]
                members = []
                for email in emails:
                    invite = records.get(email)
                    members.append({
                        'email': email,
                        'requested_role': 'annotator',
                        'scope': 'shared_team',
                        'team_id': team_id,
                        'status': invite.status if invite else 'not_invited',
                        'user_id': invite.user_id if invite else None,
                        'login': invite.login if invite else '',
                        'effective_role': invite.effective_role if invite else None,
                        'error': invite.error if invite else '',
                    })
                status = ('ready' if len(members) == 2 and
                          all(e['status'] == 'accepted' for e in members)
                          else 'needs_attention' if any(e['status'] in (
                            'invitation_error', 'role_mismatch', 'unknown', 'deactivated',
                            'identity_conflict') for e in members)
                          else 'pending' if any(e['status'] == 'pending' for e in members)
                          else 'not_invited')
                result[reg.id] = {
                    'memberships': members,
                    'membership_status': status,
                    'team_id': team_id,
                    'checked_at': max((records[e['email']].checked_at or 0 for e in members
                                      if e['email'] in records), default=0) or None,
                    'error': ' '.join(dict.fromkeys(
                        e['error'] for e in members if e['error']
                    )),
                }
            return result

    def _matches(self, listing, email, expected_id=None):
        options = []
        for record in listing:
            recorded_email = normal_email_or_blank(record.get('email') or record.get('inviteEmail'))
            rid = record.get('id') or record.get('userId')
            if recorded_email == email or (expected_id and str(rid) == str(expected_id)):
                if recorded_email and recorded_email != email:
                    raise WorkflowError('Invitation account ID conflicts with the registered email.')
                options.append(record)
        if len(options) > 1:
            raise WorkflowError('Duplicate invitation membership entries require inspection.')
        return options[0] if options else None

    def refresh(self, gateway, actor):
        """Read-only reconciliation. Never resend an invitation automatically."""
        with self.sessions() as session:
            tid = self._event_team(session)
        api = SetupGateway(gateway.api)
        listing = api.invitation_members(tid)
        now_at = now()
        registry = self._email_to_rows()
        with self.sessions.begin() as session:
            for email, registration_id in registry.items():
                row = session.get(SharedInvitation, (tid, email))
                if row is None:
                    row = SharedInvitation(team_id=tid, email=email)
                    session.add(row)
                try:
                    native = self._matches(listing, email, row.user_id)
                    if native is None:
                        if row.status in ('pending', 'accepted'):
                            row.status = 'not_found'
                        row.checked_at = now_at
                        continue
                    user_id = native.get('id') or native.get('userId')
                    active = native.get('membership_active') is True
                    role = str(native.get('role') or '').lower()
                    if active and user_id and role in (
                            'annotator', 'admin', 'manager', 'developer', 'reviewer'):
                        row.status = 'accepted'
                    elif not active:
                        row.status = 'pending'
                    else:
                        row.status = 'role_mismatch'
                    if native.get('disabled') or native.get('disabledAt'):
                        row.status = 'deactivated'
                    row.user_id = int(user_id) if user_id else row.user_id
                    row.login = str(native.get('login') or row.login or '')
                    row.effective_role = role
                    row.error = ''
                except WorkflowError as exc:
                    row.status = 'identity_conflict'
                    row.error = str(exc)
                row.checked_at = now_at
            prefs = session.get(EventPreferences, 1)
            prefs.membership_synced_at = now_at
            session.add(Audit(team_id=tid, batch_id='', actor_id=int(actor),
                action='shared_membership_sync',
                details={'registrations':len(registry)}))
        return self.summary()

    def invite(self, gateway, actor, registration_id=None):
        """Explicitly invite uninvited/rejected people once; pending isn't retried."""
        registry = self._email_to_rows()
        selected = [
            email for email, row_id in registry.items()
            if registration_id is None or row_id == registration_id
        ]
        if registration_id is not None and not selected:
            raise WorkflowError('Competition team not found in registrations.')
        with self.sessions() as session:
            tid = self._event_team(session)
        api = SetupGateway(gateway.api)
        # A native read is essential to avoid resending invitations for members
        # already invited through Supervisely or a separate organiser session.
        self.refresh(gateway, actor)
        outcomes = {'invited':0, 'pending':0, 'accepted':0, 'failed':0}
        for email in selected:
            with self.sessions() as session:
                row = session.get(SharedInvitation, (tid, email))
                state = row.status if row else 'not_invited'
            if state == 'accepted':
                outcomes['accepted'] += 1
                continue
            if state == 'pending':
                outcomes['pending'] += 1
                continue
            if state not in ('not_invited', 'invitation_error'):
                outcomes['failed'] += 1
                continue
            # Persist a checkpoint *before* the remote request. Any unknown
            # network result stays unresolved rather than being retried blindly.
            with self.sessions.begin() as session:
                row = session.get(SharedInvitation, (tid, email))
                row.status = 'unknown'
                row.error = 'Invitation sent or outcome unknown. Refresh native membership before retrying.'
            try:
                reply = api.invite_email(tid, email, 'annotator')
            except InvitationRejected as exc:
                with self.sessions.begin() as session:
                    row = session.get(SharedInvitation, (tid, email))
                    row.status, row.error = 'invitation_error', str(exc)
                outcomes['failed'] += 1
                continue
            except Exception:
                outcomes['failed'] += 1
                continue
            with self.sessions.begin() as session:
                row = session.get(SharedInvitation, (tid, email))
                row.status = 'pending'
                row.invited_at = now()
                row.user_id = reply.get('user_id') or row.user_id
                row.error = ''
                session.add(Audit(team_id=tid, batch_id='', actor_id=int(actor),
                    action='shared_email_invited', details={'email':email}))
            outcomes['invited'] += 1
        self.refresh(gateway, actor)
        return outcomes


def normal_email_or_blank(value):
    value = str(value or '').strip().casefold()
    if not value:
        return ''
    return normal_email(value)
