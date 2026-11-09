"""CSV-driven native invitations and verified event roles in one shared team.

All live invitation writes are explicit, use the role declared in the imported
CSV, and never create a new native team. DEMO and PRODUCTION use the same path.
"""
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from .db import (
    Audit, EventConfig, EventInvitee, EventMember, EventPreferences,
    Registration, RosterContact, SharedInvitation, TeamOnboarding, now,
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

    def _targets(self, session, registration_id=None, audience='everyone', email=None,
                 selected_emails=None):
        """One native invite per identity even if a monitor oversees many pairs.

        A submitted selection is an explicit non-empty allowlist. It must never
        fall back to the 'everyone' audience when it is empty or malformed.
        Validate the entire batch BEFORE any native writes or membership refresh.
        """
        invitees = list(session.scalars(select(EventInvitee).order_by(EventInvitee.email)))
        if not invitees:
            raise WorkflowError('Upload a roster before sending invitations.')
        if selected_emails is not None:
            if (not isinstance(selected_emails, list) or
                    not 1 <= len(selected_emails) <= 1000):
                raise WorkflowError('Select between 1 and 1000 eligible roster people to invite.')
            if any(not isinstance(value, str) for value in selected_emails):
                raise WorkflowError('Selected invitation addresses must be text.')
            addresses = [normal_email(value) for value in selected_emails]
            if len(set(addresses)) != len(addresses):
                raise WorkflowError('The selected invitation list contains duplicate accounts.')
            allowed = {person.email: person for person in invitees}
            unknown = set(addresses) - allowed.keys()
            if unknown:
                raise WorkflowError(
                    'One or more selected accounts are not prepared for invitations. '
                    'Prepare complete eligible pairs and review their roles first.'
                )
            # The imported workbook may flag cancelled, unconsented, duplicate,
            # or inactive records. None may be explicitly invited by bypassing
            # the roster UI's disabled checkbox.
            from .db import EventRosterRecord
            roster = list(session.scalars(select(EventRosterRecord).where(
                EventRosterRecord.email.in_(addresses))))
            if any(person.needs_review or person.event_role == 'inactive'
                   for person in roster):
                raise WorkflowError(
                    'Some selected roster records need review and cannot be invited.'
                )
            if (registration_id is not None or audience != 'everyone' or
                    email is not None):
                raise WorkflowError(
                    'Select invitees by explicit emails or by an audience, not both.'
                )
            return [allowed[address] for address in addresses]
        if registration_id is not None:
            reg_id = str(registration_id)
            if not session.get(Registration, reg_id):
                raise WorkflowError('Competition pair was not found.')
            invitees = [v for v in invitees if reg_id in (v.registration_ids or [])]
        if audience == 'monitors':
            invitees = [v for v in invitees if v.event_role in ('monitor', 'participant_monitor')]
        elif audience == 'participants':
            invitees = [v for v in invitees if v.event_role in ('participant', 'participant_monitor')]
        elif audience == 'person':
            address = normal_email(email)
            invitees = [v for v in invitees if v.email == address]
            if not invitees:
                raise WorkflowError('This person is not in the imported roster.')
        elif audience != 'everyone':
            raise WorkflowError('Choose monitors, participants, everyone or one person.')
        return invitees

    def people(self):
        """Show imported people immediately, without making any native request."""
        with self.sessions() as session:
            config = session.get(EventConfig, 1)
            tid = config.monitoring_team_id if config else None
            registrations = {r.id: r for r in session.scalars(select(Registration))}
            invitations = {r.email: r for r in session.scalars(select(SharedInvitation).where(
                SharedInvitation.team_id == tid))} if tid else {}
            names = {m['email']: m.get('name') or '' for r in registrations.values()
                     for m in r.members if m.get('email')}
            contacts = list(session.scalars(select(RosterContact)))
            names.update({p.email:p.name for p in contacts if p.email and p.name})
            result = []
            for entry in session.scalars(select(EventInvitee).order_by(EventInvitee.email)):
                invited = invitations.get(entry.email)
                result.append({'id': entry.email,
                    'email': entry.email, 'name': names.get(entry.email) or entry.login or entry.email,
                    'login': entry.login, 'role': entry.event_role,
                    'teams': [registrations[rid].team_name for rid in entry.registration_ids if rid in registrations],
                    'status': invited.status if invited else 'not_invited',
                    'error': invited.error if invited else '',
                })
            for contact in contacts:
                if not contact.email:
                    result.append({'id': contact.id, 'email': '', 'name': contact.name,
                        'login': '', 'role': contact.role, 'teams': [contact.team_name] if contact.team_name else [],
                        'status': 'missing_email', 'error': 'Email missing in the roster. Add it and upload the file again to invite this person.'})
            return result

    def summary(self):
        with self.sessions() as session:
            team_id = self._event_team(session)
            records = {v.email: v for v in session.scalars(
                select(SharedInvitation).where(SharedInvitation.team_id == team_id)
            )}
            declared = {v.email: v for v in session.scalars(select(EventInvitee))}
            onboarding_rows = {r.registration_id:r for r in session.scalars(select(TeamOnboarding))}
            result = {}
            for reg in session.scalars(select(Registration).order_by(Registration.team_name)):
                people = [normal_email(m.get('email')) for m in reg.members if m.get('email')]
                onboard = onboarding_rows.get(reg.id)
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
                                   'reviewer' if role == 'participant_monitor' else
                                   'manager' if role == 'monitor' else 'annotator')
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
    def _matches(listing, entry, previous_user_id=None, previous_login=None):
        matches = []
        for native in listing:
            email = normal_email_or_blank(native.get('email') or native.get('inviteEmail'))
            uid = native.get('userId') or native.get('id')
            login = str(native.get('login') or '').strip().casefold()
            expected_login = str(entry.login or previous_login or '').strip().casefold()
            if (email == entry.email or
                    (expected_login and login == expected_login) or
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
                    native = self._matches(listing, entry, row.user_id, row.login)
                    if native is None:
                        # An invitation can take time to appear in native
                        # membership listings. Pending is never retried blindly.
                        # A previously accepted identity disappearing is a
                        # distinct access-review condition.
                        if row.status == 'accepted':
                            row.status = 'not_found'
                        if row.user_id and row.user_id != config.owner_id:
                            former = session.get(EventMember, int(row.user_id))
                            if former and former.group == 'monitors':
                                former.group = 'annotators'
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
                    if row.user_id and row.user_id != config.owner_id:
                        former = session.get(EventMember, int(row.user_id))
                        if former and former.group == 'monitors':
                            former.group = 'annotators'
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
                        # Use the first accepted participant with monitoring
                        # duties when the CSV has no separate monitor.
                        for person in reg.members:
                            if person.get('role') == 'participant_monitor' and person.get('email'):
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

    def invite(self, gateway, actor, registration_id=None, audience='everyone', email=None,
               selected_emails=None):
        """Invite only explicitly chosen accounts when selected_emails is supplied.

        Native permissions still come from the reviewed EventInvitee role,
        never from request data. Pending or unknown writes are never resent.
        """
        with self.sessions() as session:
            tid = self._event_team(session)
            selected = [v.email for v in self._targets(
                session, registration_id, audience, email, selected_emails)]
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
                claimed = session.execute(update(SharedInvitation).where(
                    SharedInvitation.team_id == tid, SharedInvitation.email == email,
                    SharedInvitation.status.in_(('not_invited', 'invitation_error'))
                ).values(status='unknown', error='Native invitation outcome unconfirmed. Inspect before retrying.'))
                if claimed.rowcount != 1:
                    outcomes['failed'] += 1
                    continue
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
                row.login = reply.get('login') or row.login
                row.error = ''
                session.add(Audit(team_id=tid, batch_id='', actor_id=int(actor),
                    action='shared_email_invited',
                    details={'email': email, 'native_role': native_role}))
            outcomes['invited'] += 1
        self.refresh(gateway, actor)
        return outcomes
