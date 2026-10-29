"""Email invitations with durable teams, pending acceptance and explicit refresh.

No instance-wide user enumeration is used. Native IDs come from the invitation
response and the target teams' accepted membership lists.
"""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from .db import Audit, EventMember, Registration, Team, TeamOnboarding, now
from .service import WorkflowError
from .setup_gateway import InvitationRejected


def find_member(records, entry):
    candidates = []
    for record in records:
        email = str(record.get('email') or record.get('inviteEmail') or '').strip().casefold()
        uid = record.get('id') or record.get('userId')
        if email == entry['email'] or (entry.get('user_id') and uid == entry['user_id']):
            if email and entry['email'] and email != entry['email']:
                raise WorkflowError('A native membership ID has a different email. Inspect the account association.')
            candidates.append(record)
    if len(candidates) > 1:
        raise WorkflowError('Multiple native membership records match this invitation. Inspect the team before continuing.')
    return candidates[0] if candidates else None


def observe(record, entry):
    result = dict(entry)
    if record is None:
        result['status'] = 'not_found' if entry['status'] in ('accepted', 'pending') else entry['status']
        return result
    result['user_id'] = record.get('id') or record.get('userId') or entry.get('user_id')
    result['login'] = record.get('login') or ''
    result['name'] = record.get('name') or result['login'] or entry['email']
    result['effective_role'] = record.get('role')
    result['error'] = ''
    if record.get('disabled') or record.get('disabledAt'):
        result['status'] = 'deactivated'
    elif not record.get('membership_active'):
        result['status'] = 'pending'
    elif not result.get('user_id'):
        result['status'] = 'unknown'
    elif record.get('role') not in ({'admin', 'manager'} if entry['requested_role'] == 'manager' else {'annotator'}):
        result['status'] = 'role_mismatch'
        result['error'] = 'Native role differs from the requested event role. Correct it in Supervisely and refresh.'
    else:
        result['status'] = 'accepted'
    return result


class EmailOnboarding:
    def __init__(self, sessions):
        self.sessions = sessions

    def provision(self, gateway, setup, actor, registration_id, monitor_id=0):
        """Create once, then invite unresolved memberships; pending is a success."""
        setup.authorize(gateway, actor)
        groups = setup.groups()
        if not groups['ready']:
            raise WorkflowError('Create the Monitors and Annotators event groups first.')
        with self.sessions() as session:
            registration = session.get(Registration, registration_id)
            if not registration:
                raise WorkflowError('Registration not found.')
            plan = session.get(TeamOnboarding, registration_id)
            if plan is None:
                plan = TeamOnboarding(registration_id=registration_id, manager_email='')
            manager_email = plan.manager_email
            chosen = int(monitor_id or registration.monitor_id or 0)
            if registration.team_id:
                if monitor_id and registration.monitor_id and chosen != registration.monitor_id:
                    raise WorkflowError('Use monitor handover to change an existing team assignment.')
                create = False
            else:
                create = True
                if session.scalar(select(Team.id).where(Team.name == registration.team_name).limit(1)):
                    raise WorkflowError('A native participant team with this name is already registered. Inspect it before creating another.')
        if chosen:
            if chosen not in {p['id'] for p in groups['monitors']}:
                raise WorkflowError('Select a registered monitor.')
            profile = gateway.monitor(setup.monitoring_team_id, chosen)
            actual_email = str(getattr(profile, 'email', '') or '').strip().casefold()
            if manager_email and actual_email and manager_email != actual_email:
                raise WorkflowError('The selected monitor has a different email from this registration.')
            manager_email = manager_email or actual_email
        elif create:
            # A known organiser can also be the requested demo monitor. Other
            # emails are invited without a global account search.
            profile = gateway.api.user.get_my_info()
            if manager_email and str(getattr(profile, 'email', '') or '').strip().casefold() == manager_email:
                chosen = int(profile.id)
        if not manager_email:
            raise WorkflowError('Provide monitor_email in the CSV or select a monitor whose email is visible.')
        if manager_email in {p['email'] for p in registration.members}:
            raise WorkflowError('The monitor must be distinct from all annotators.')
        with setup.operation(actor, uuid4().hex, 'email_team', {'registration_id': registration_id}) as checkpoint:
            with self.sessions() as session:
                if session.get(Registration, registration_id).team_id:
                    create = False
            if create:
                remote = gateway.api.team.create(registration.team_name,
                    description=f'Monitoring event email registration {registration_id}')
                checkpoint({'team_id': remote.id})
                entries = [dict(email=p['email'], requested_role='annotator', scope='team', team_id=remote.id,
                                user_id=p.get('user_id'), status='not_invited') for p in registration.members]
                entries += [dict(email=manager_email, requested_role='manager', scope=scope,
                                 team_id=tid, user_id=chosen or None, status='not_invited')
                            for scope, tid in [('team', remote.id), ('monitors', setup.monitoring_team_id)]]
                with self.sessions.begin() as session:
                    current = session.get(Registration, registration_id)
                    if current.team_id:
                        raise WorkflowError('Another action created this team. Inspect the native resource checkpoint.')
                    session.add(Team(id=remote.id, name=remote.name, monitor_id=chosen, annotator_ids=[]))
                    current.team_id, current.monitor_id = remote.id, chosen
                    stored = session.get(TeamOnboarding, registration_id)
                    if stored is None:
                        stored = TeamOnboarding(registration_id=registration_id)
                        session.add(stored)
                    stored.manager_email, stored.memberships, stored.status = manager_email, entries, 'pending'
                    session.add(Audit(team_id=remote.id, batch_id='', actor_id=actor, action='email_team_created',
                                      details={'registration_id': registration_id}))
            self._refresh(gateway, registration_id, actor, invite=True, checkpoint=checkpoint)
        with self.sessions() as session:
            return session.get(Registration, registration_id).team_id

    def refresh(self, gateway, setup, actor, registration_id=None):
        setup.authorize(gateway, actor)
        with self.sessions() as session:
            ids = list(session.scalars(select(TeamOnboarding.registration_id))) if registration_id is None else [registration_id]
        # Only read native memberships. A read failure is visible as Unknown and
        # never triggers another invitation, team creation or a global setup lock.
        for key in ids:
            with self.sessions() as session:
                registration = session.get(Registration, key)
                plan = session.get(TeamOnboarding, key)
                if not registration or not registration.team_id or not plan or not plan.memberships:
                    continue
            with setup.operation(actor, uuid4().hex, 'invitation_refresh', {'registration_id': key}):
                self._refresh(gateway, key, actor)

    def _save_entries(self, registration_id, entries):
        with self.sessions.begin() as session:
            session.get(TeamOnboarding, registration_id).memberships = deepcopy(entries)

    def _refresh(self, gateway, registration_id, actor, invite=False, checkpoint=None):
        with self.sessions() as session:
            plan = session.get(TeamOnboarding, registration_id)
            entries = deepcopy(plan.memberships)
        cache = {}
        for index, entry in enumerate(entries):
            tid = entry['team_id']
            try:
                if tid not in cache:
                    cache[tid] = gateway.invitation_members(tid)
                record = find_member(cache[tid], entry)
                observed = observe(record, entry)
            except Exception:
                entries[index] = dict(entry, status='unknown', error='Could not confirm native membership. Refresh invitations to retry; no invitation was resent.')
                continue
            if record is None and invite and entry['status'] in ('not_invited', 'invitation_error', 'not_found'):
                try:
                    reply = gateway.invite_email(tid, entry['email'], entry['requested_role'])
                    observed = dict(entry, status='pending', user_id=reply.get('user_id') or entry.get('user_id'), error='')
                    entries[index] = observed
                    self._save_entries(registration_id, entries)
                    checkpoint({'team_id': tid, 'email': entry['email'], 'requested_role': entry['requested_role'], 'invitation_sent': True})
                    cache.pop(tid, None)
                    try:
                        cache[tid] = gateway.invitation_members(tid)
                        found = find_member(cache[tid], observed)
                        if found is not None:
                            observed = observe(found, observed)
                    except Exception:
                        observed['error'] = 'Invitation requested. Membership could not yet be confirmed; refresh without resending.'
                except InvitationRejected as exc:
                    observed = dict(entry, status='invitation_error', error=str(exc))
                except Exception:
                    entries[index] = dict(entry, status='unknown', error='Invitation outcome is uncertain. Inspect the native team and setup history before retrying.')
                    self._save_entries(registration_id, entries)
                    raise
            entries[index] = observed
        self._publish(registration_id, entries, actor)

    def _publish(self, registration_id, entries, actor):
        ready = bool(entries) and all(e['status'] == 'accepted' for e in entries)
        managers = [e for e in entries if e['requested_role'] == 'manager']
        participants = [e for e in entries if e['requested_role'] == 'annotator']
        manager_ids = {e.get('user_id') for e in managers}
        participant_ids = [e.get('user_id') for e in participants]
        if ready and (len(manager_ids) != 1 or None in manager_ids or not participant_ids
                      or len(set(participant_ids)) != len(participant_ids) or manager_ids.intersection(participant_ids)):
            ready = False
            for e in entries:
                e['status'], e['error'] = 'identity_mismatch', 'Accepted memberships do not identify distinct event participants and the same monitor.'
        status = 'ready' if ready else ('pending' if all(e['status'] in ('accepted', 'pending', 'not_invited') for e in entries) else 'needs_attention')
        with self.sessions.begin() as session:
            plan = session.get(TeamOnboarding, registration_id)
            registration = session.get(Registration, registration_id)
            team = session.get(Team, registration.team_id)
            old_status = plan.status
            if ready:
                mid = next(iter(manager_ids))
                used = {uid for existing_team in session.scalars(select(Team).where(Team.id != team.id)) for uid in existing_team.annotator_ids}
                if used.intersection(participant_ids):
                    raise WorkflowError('An accepted native account is already an annotator in another event team.')
                if team.annotator_ids != participant_ids or team.monitor_id != mid:
                    team.annotator_ids, team.monitor_id = participant_ids, mid
                    team.revision += 1
                registration.monitor_id = mid
                for e in participants + managers[:1]:
                    existing = session.get(EventMember, e['user_id'])
                    group = 'annotators' if e['requested_role'] == 'annotator' else 'monitors'
                    if existing and existing.group != group:
                        raise WorkflowError('An accepted account already has the opposite event role. Inspect the roster.')
                    if existing is None:
                        session.add(EventMember(user_id=e['user_id'], group=group, login=e['login'], name=e.get('name') or e['login']))
                registration.members = [dict(m, user_id=e['user_id'], login=e.get('login') or '', status='accepted')
                                        for m, e in zip(registration.members, participants)]
            if old_status == 'ready' and not ready:
                team.revision += 1
            plan.memberships, plan.status, plan.checked_at = entries, status, now()
            registration.error = ' '.join(dict.fromkeys(e.get('error', '') for e in entries if e.get('error')))
            if old_status != status:
                session.add(Audit(team_id=team.id, batch_id='', actor_id=actor, action='membership_status', details={'status': status}))

    def verify_ready(self, gateway, team_id):
        """Fresh native check before copying data or releasing a native job."""
        with self.sessions() as session:
            registration = session.scalar(select(Registration).where(Registration.team_id == team_id))
            plan = session.get(TeamOnboarding, registration.id) if registration else None
            if not plan or not plan.memberships:
                return  # Existing legacy registration, governed by its native checks.
            if plan.status != 'ready':
                raise WorkflowError('This team is waiting for accepted invitations and correct roles. Refresh invitations first.')
            entries = deepcopy(plan.memberships)
            team = session.get(Team, team_id)
            expected = [e['user_id'] for e in entries if e['requested_role'] == 'annotator']
            managers = {e['user_id'] for e in entries if e['requested_role'] == 'manager'}
            if team.annotator_ids != expected or managers != {team.monitor_id}:
                raise WorkflowError('The registered task owners differ from the accepted invitation records. Refresh or inspect the assignment.')
        cache = {}
        for entry in entries:
            if entry['team_id'] not in cache:
                cache[entry['team_id']] = gateway.invitation_members(entry['team_id'])
            check = observe(find_member(cache[entry['team_id']], entry), entry)
            if check['status'] != 'accepted' or check.get('user_id') != entry.get('user_id'):
                raise WorkflowError('A native invitation or role changed. Refresh invitations before distributing or releasing work.')

    def handover(self, gateway, session, registration, monitor):
        plan = session.get(TeamOnboarding, registration.id)
        if not plan or not plan.memberships:
            return
        email = str(getattr(monitor, 'email', '') or '').strip().casefold()
        entries = deepcopy(plan.memberships)
        for index, entry in enumerate(entries):
            if entry['requested_role'] != 'manager':
                continue
            changed = dict(entry, email=email, user_id=monitor.id, status='accepted')
            observed = observe(find_member(gateway.invitation_members(entry['team_id']), changed), changed)
            if observed['status'] != 'accepted':
                raise WorkflowError('The replacement monitor has not accepted every required native membership.')
            entries[index] = observed
        plan.manager_email, plan.memberships, plan.checked_at = email, entries, now()
        registration.monitor_id = monitor.id
