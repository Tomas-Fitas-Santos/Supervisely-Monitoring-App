"""CSV-driven native invitations and verified event roles in one shared team.

All live invitation writes are explicit, use the role declared in the imported
CSV, and never create a new native team. DEMO and PRODUCTION use the same path.
"""
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from .db import (
    Audit, EventConfig, EventInvitee, EventMember, EventPreferences,
    Registration, SharedInvitation, TeamOnboarding, now,
)
from .event_settings import single_team
from .service import WorkflowError
from .setup_gateway import InvitationRejected, SetupGateway


def normal_email(email):
    email = str(email or '').strip().casefold()
    if (len(email) > 254 or email.count('@') != 1 or
            not all(email.split('@', 1)) or any(c.isspace() for c in email)):
        raise WorkflowError('The roster contains an invalid email address.')
    return email


def normal_email_or_blank(value):
    if not value:
        return ''
    try:
        return normal_email(value)
    except WorkflowError:
        # An unrelated native record may have a login rather than an email.
        return ''


class SharedTeamInvitations:
    def __init__(self, sessions):
        self.sessions = sessions

    def _event_team(self, session):
        config = session.get(EventConfig, 1)
        if not single_team(config):
            raise WorkflowError(
                'Configure the existing shared Supervisely Team ID in Settings first.'
            )
        return int(config.monitoring_team_id)

    def _targets(self, session, registration_id=None):
        """One native invite per identity even if a monitor oversees many pairs."""
        invitees = list(session.scalars(select(EventInvitee).order_by(EventInvitee.email)))
        if not invitees:
            raise WorkflowError('Import the current CSV with explicit roles first.')
        if registration_id is not None:
            reg_id = str(registration_id)
            if not session.get(Registration, reg_id):
                raise WorkflowError('Competition pair was not found.')
            invitees = [v for v in invitees if reg_id in (v.registration_ids or [])]
        return invitees

    def summary(self):
        with self.sessions() as session:
            team_id = self._event_team(session)
            records = {v.email: v for v in session.scalars(
                select(SharedInvitation).where(SharedInvitation.team_id == team_id)
            )}
            declared = {v.email: v for v in session.scalars(select(EventInvitee))}
            result = {}
            for reg in session.scalars(select(Registration).order_by(Registration.team_name)):
                people = [normal_email(m.get('email')) for m in reg.members if m.get('email')]
                onboard = session.get(TeamOnboarding, reg.id)
                monitor_email = normal_email_or_blank(onboard.manager_email if onboard else '')
                emails = list(dict.fromkeys(people + ([monitor_email] if monitor_email else [])))
                members = []
                for email in emails:
                    target = declared.get(email)
                    invited = records.get(email)
                    role = (target.event_role if target else
                            next((p.get('role', 'participant') for p in reg.members
                                  if normal_email_or_blank(p.get('email')) == email), 'monitor'))
                    native_role = (target.native_role if target else
                                   'manager' if role != 'participant' else 'annotator')
                    members.append({
                        'email': email,
                        'login': target.login if target else '',
                        'event_role': role,
                        'requested_role': native_role,
                        'scope': 'shared_team',
                        'team_id': team_id,
                        'status': invited.status if invited else 'not_invited',
                        'user_id': invited.user_id if invited else None,
                        'effective_role': invited.effective_role if invited else '',
                        'error': invited.error if invited else '',
                    })
                ready = (len(people) == 2 and len(set(people)) == 2
                         and all(m['status'] == 'accepted' for m in members))
                status = (
                    'ready' if ready else
                    'needs_attention' if any(m['status'] in (
                        'invitation_error', 'role_mismatch', 'unknown',
                        'deactivated', 'identity_conflict', 'not_found',
                    ) for m in members) else
                    'pending' if any(m['status'] == 'pending' for m in members) else
                    'not_invited'
                )
                result[reg.id] = {
                    'memberships': members,
                    'membership_status': status,
                    'team_id': team_id,
                    'checked_at': max((records[e].checked_at or 0 for e in emails
                                      if e in records), default=0) or None,
                    'error': ' '.join(dict.fromkeys(m['error'] for m in members if m['error'])),
                }
            return result

    @staticmethod
    def _matches(listing, entry, previous_user_id=None):
        matches = []
        for native in listing:
            email = normal_email_or_blank(native.get('email') or native.get('inviteEmail'))
            uid = native.get('userId') or native.get('id')
            login = str(native.get('login') or '').strip().casefold()
            if (email == entry.email or
                    (entry.login and login == entry.login.casefold()) or
                    (previous_user_id and str(uid) == str(previous_user_id))):
                if email and email != entry.email:
                    raise WorkflowError(
                        'Native membership email does not match the registered CSV address.'
                    )
                if entry.login and login and login != entry.login.casefold():
                    raise WorkflowError(
                        'Native membership login does not match the registered CSV login.'
                    )
                matches.append(native)
        if len(matches) > 1:
            raise WorkflowError('Duplicate membership records require administrator review.')
        return matches[0] if matches else None

    @staticmethod
    def _role_accepted(entry, actual_role, user_id, organiser_id):
        # CSV must never grant a native Admin role. Preserve an already-native
        # organiser Admin rather than downgrading them when listed as a monitor.
        if actual_role == entry.native_role:
            return True
        return (actual_role == 'admin' and
                organiser_id is not None and user_id is not None and
                int(user_id) == int(organiser_id) and
                entry.event_role in ('monitor', 'participant_monitor'))

    def refresh(self, gateway, actor):
        """Read-only native reconciliation; update app permissions from evidence."""
        with self.sessions() as session:
            tid = self._event_team(session)
        listing = SetupGateway(gateway.api).invitation_members(tid)
        try:
            native_role_ids = {
                str(role.id): str(role.role).lower()
                for role in gateway.api.role.get_list()
            }
        except Exception:
            native_role_ids = {}
        now_at = now()
        with self.sessions.begin() as session:
            config = session.get(EventConfig, 1)
            entries = self._targets(session)
            for entry in entries:
                row = session.get(SharedInvitation, (tid, entry.email))
                if row is None:
                    row = SharedInvitation(team_id=tid, email=entry.email)
                    session.add(row)
                try:
                    native = self._matches(listing, entry, row.user_id)
                    if native is None:
                        if row.status in ('pending', 'accepted'):
                            row.status = 'not_found'
                        row.checked_at = now_at
                        continue
                    uid = native.get('userId') or native.get('id')
                    raw_role = native.get('role') or native.get('roleId') or ''
                    if isinstance(raw_role, dict):
                        raw_role = raw_role.get('role') or raw_role.get('name') or raw_role.get('id') or ''
                    role = str(raw_role).strip().casefold()
                    role = native_role_ids.get(role, role)
                    active = native.get('membership_active') is True
                    if native.get('disabled') or native.get('disabledAt'):
                        status = 'deactivated'
                    elif not active:
                        status = 'pending'
                    elif not uid or not role:
                        status = 'role_mismatch'
                    elif self._role_accepted(entry, role, uid, config.owner_id):
                        status = 'accepted'
                    else:
                        status = 'role_mismatch'
                    row.status = status
                    row.effective_role = role
                    row.user_id = int(uid) if uid else row.user_id
                    row.login = str(native.get('login') or row.login or '')
                    row.error = (
                        f'CSV requests native {entry.native_role}, but Supervisely '
                        f'reports {role or "unknown"}. An Admin must review the role.'
                        if status == 'role_mismatch' else ''
                    )
                    if uid:
                        member = session.get(EventMember, int(uid))
                        authorised_monitor = (
                            status == 'accepted' and entry.event_role in
                            ('monitor', 'participant_monitor')
                        )
                        if status == 'accepted' or member:
                            # A native downgrade or lost acceptance must revoke
                            # event monitoring privileges on reconciliation.
                            group = 'monitors' if authorised_monitor else 'annotators'
                            if member:
                                member.group = group
                                if row.login:
                                    member.login = row.login
                                member.name = str(native.get('name') or row.login or member.name)
                            elif row.login:
                                session.add(EventMember(
                                    user_id=int(uid), group=group, login=row.login,
                                    name=str(native.get('name') or row.login),
                                ))
                except WorkflowError as exc:
                    row.status = 'identity_conflict'
                    row.error = str(exc)
                row.checked_at = now_at

            # Reflect verified native account identity in the logical pair,
            # which is required for check-in, staff balancing and job planning.
            for reg in session.scalars(select(Registration)):
                updated = False
                for person in reg.members:
                    email = normal_email_or_blank(person.get('email'))
                    checked = session.get(SharedInvitation, (tid, email))
                    if checked:
                        if checked.status == 'accepted':
                            person['user_id'] = checked.user_id
                            person['login'] = checked.login or person.get('login', '')
                        if person.get('status') != checked.status:
                            person['status'] = checked.status
                        updated = True
                if updated:
                    flag_modified(reg, 'members')
                if not reg.monitor_id:
                    onboarding = session.get(TeamOnboarding, reg.id)
                    target = normal_email_or_blank(onboarding.manager_email if onboarding else '')
                    if target:
                        invite = session.get(SharedInvitation, (tid, target))
                        if invite and invite.status == 'accepted' and invite.user_id:
                            reg.monitor_id = int(invite.user_id)
                    else:
                        # DEMO can start with both pair members being monitors.
                        # Assign the first accepted dual-role participant; the
                        # second remains available for manual reassignment.
                        import os
                        if os.getenv('MONITORING_PILOT') == '1':
                            for person in reg.members:
                                if person.get('role') == 'participant_monitor':
                                    invited = session.get(SharedInvitation, (
                                        tid, normal_email(person['email'])))
                                    if invited and invited.status == 'accepted' and invited.user_id:
                                        reg.monitor_id = int(invited.user_id)
                                        break
            prefs = session.get(EventPreferences, 1)
            prefs.membership_synced_at = now_at
            session.add(Audit(team_id=tid, batch_id='', actor_id=int(actor),
                action='shared_membership_sync',
                details={'invitees_checked': len(entries)}))
        return self.summary()

    def invite(self, gateway, actor, registration_id=None):
        """Invite roles from CSV, once, with no blind retry of unknown writes."""
        with self.sessions() as session:
            tid = self._event_team(session)
            selected = [v.email for v in self._targets(session, registration_id)]
        api = SetupGateway(gateway.api)
        self.refresh(gateway, actor)
        outcomes = {'invited': 0, 'pending': 0, 'accepted': 0, 'failed': 0}
        for email in selected:
            with self.sessions() as session:
                row = session.get(SharedInvitation, (tid, email))
                target = session.get(EventInvitee, email)
                state = row.status if row else 'not_invited'
                native_role, login = target.native_role, target.login
            if state == 'accepted':
                outcomes['accepted'] += 1
                continue
            if state == 'pending':
                outcomes['pending'] += 1
                continue
            if state not in ('not_invited', 'invitation_error'):
                outcomes['failed'] += 1
                continue
            with self.sessions.begin() as session:
                row = session.get(SharedInvitation, (tid, email))
                row.status = 'unknown'
                row.error = 'Native invitation outcome unconfirmed. Inspect before retrying.'
            try:
                reply = api.invite_email(tid, email, native_role, login=login or None)
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
                    action='shared_email_invited',
                    details={'email': email, 'native_role': native_role}))
            outcomes['invited'] += 1
        self.refresh(gateway, actor)
        return outcomes
