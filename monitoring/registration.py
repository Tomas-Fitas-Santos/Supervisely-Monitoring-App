"""CSV column mapping, exact email matching and idempotent pair provisioning."""
import csv
import hashlib
import io
from sqlalchemy import select
from .db import Audit, EventInvitee, Registration, SharedInvitation, Team, TeamOnboarding
from .service import WorkflowError

COLUMNS = ['team_name', 'member_1_email', 'member_2_email', 'member_1_name', 'member_2_name',
           'member_1_login', 'member_2_login', 'member_1_role', 'member_2_role',
           'monitor_email', 'monitor_login']
PERSON_COLUMNS = ['team_name', 'participant_email', 'participant_name', 'participant_login',
                  'participant_role']

ROLE_ALIASES = {
    'participant': 'participant', 'annotator': 'participant',
    'participant_monitor': 'participant_monitor', 'participant+monitor': 'participant_monitor',
    'annotator+monitor': 'participant_monitor', 'both': 'participant_monitor',
}


def participant_role(raw, required=False):
    role = str(raw or '').strip().casefold().replace(' ', '_')
    if not role and not required:
        return 'participant'  # Legacy files without a role column.
    if role not in ROLE_ALIASES:
        raise WorkflowError('Use participant or participant_monitor for every pair member. '
                            'Use monitor_email for monitor-only staff.')
    return ROLE_ALIASES[role]



class Registrations:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def columns(text):
        if not isinstance(text, str) or len(text.encode()) > 2 * 1024 * 1024:
            raise WorkflowError('Choose a UTF-8 CSV smaller than 2 MiB.')
        stream = io.StringIO(text.lstrip('\ufeff'))
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(stream, dialect=dialect)
        if reader.fieldnames:
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise WorkflowError('CSV needs distinct column headers.')
        rows = list(reader)
        if len(rows) > 10000 or not rows:
            raise WorkflowError('CSV must contain between 1 and 10000 team rows.')
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise WorkflowError('Some CSV rows do not match the header columns.')
        return reader.fieldnames, rows

    def import_csv(self, actor, text, mapping):
        """Import declared event roles before any native invitations are sent.

        The pair row lists two participants, who can independently have
        monitoring duties (participant_monitor). The optional monitor_email
        identifies a separate monitor, reusable across multiple pairs.
        Native Admin is NEVER granted by CSV.
        """
        headers, raw = self.columns(text)
        if not isinstance(mapping, dict) or set(mapping) - set(COLUMNS + PERSON_COLUMNS):
            raise WorkflowError('Invalid CSV column mapping.')
        if mapping.get('participant_email'):
            if mapping.get('team_name') not in headers or mapping['participant_email'] not in headers:
                raise WorkflowError('Map the team and participant email columns.')
            grouped = {}
            for row in raw:
                name = str(row.get(mapping['team_name'], '') or '').strip()
                grouped.setdefault(name, []).append(row)
            converted = []
            for name, people in grouped.items():
                if len(people) != 2:
                    raise WorkflowError(f'Team {name or "(unnamed)"} must have exactly two participant rows.')
                item = {'team_name': name}
                for i, person in enumerate(people, 1):
                    for field in ('email', 'name', 'login', 'role'):
                        item[f'member_{i}_{field}'] = person.get(mapping.get(f'participant_{field}', ''), '')
                converted.append(item)
            role_mapped = bool(mapping.get('participant_role') in headers)
            raw, headers = converted, COLUMNS
            mapping = {field: field for field in COLUMNS
                       if not field.endswith('_role') or role_mapped}
        for field in COLUMNS[:2]:
            if mapping.get(field) not in headers:
                raise WorkflowError('Map the team name and first participant email column.')
        used = [mapping[f] for f in COLUMNS if mapping.get(f) in headers]
        if len(set(used)) != len(used):
            raise WorkflowError('Map each CSV column to only one field.')

        rows, names, participant_emails = [], set(), set()
        for number, raw_row in enumerate(raw, 2):
            row = {field: str(raw_row.get(mapping.get(field, ''), '') or '').strip()
                   for field in COLUMNS}
            name = row['team_name']
            if not name or len(name) > 180 or name.casefold() in names:
                raise WorkflowError(f'Row {number}: missing or duplicate team name.')
            names.add(name.casefold())
            members = []
            for i in (1, 2):
                email = row[f'member_{i}_email'].casefold()
                if i == 2 and not email:
                    continue
                if (len(email) > 254 or email.count('@') != 1 or
                        not all(email.split('@', 1)) or any(c.isspace() for c in email) or
                        email in participant_emails):
                    raise WorkflowError(f'Row {number}: invalid or duplicate participant email.')
                participant_emails.add(email)
                role_col = f'member_{i}_role'
                role_declared = bool(mapping.get(role_col) in headers)
                role = participant_role(row[role_col], required=role_declared)
                members.append({
                    'email': email, 'name': row[f'member_{i}_name'],
                    'login': row[f'member_{i}_login'], 'role': role,
                    'user_id': None, 'status': 'unresolved',
                })
            if not members:
                raise WorkflowError(f'Row {number}: at least one participant is required.')
            manager = row['monitor_email'].casefold()
            if row['monitor_login'] and not manager:
                raise WorkflowError(
                    f'Row {number}: monitor_login requires monitor_email.'
                )
            if manager and (len(manager) > 254 or manager.count('@') != 1
                            or not all(manager.split('@', 1))
                            or any(c.isspace() for c in manager)):
                raise WorkflowError(f'Row {number}: invalid monitor email.')
            if manager in {m['email'] for m in members} and not any(
                    m['email'] == manager and m['role'] == 'participant_monitor'
                    for m in members):
                raise WorkflowError(
                    f'Row {number}: the same person can be a participant and monitor '
                    'only with member role participant_monitor.'
                )
            rows.append((
                hashlib.sha256(name.casefold().encode()).hexdigest(),
                name, members, manager, row['monitor_login'],
            ))

        with self.sessions.begin() as s:
            existing = list(s.scalars(select(Registration)))
            for key, name, members, manager, monitor_login in rows:
                for other in existing:
                    if other.id != key and {m['email'] for m in other.members}.intersection(
                            m['email'] for m in members):
                        raise WorkflowError('A participant is already registered to another pair.')
                reg = s.get(Registration, key)
                if reg:
                    if [m['email'] for m in reg.members] != [m['email'] for m in members]:
                        raise WorkflowError('This team name has different members. Resolve its roster explicitly.')
                    onboard = s.get(TeamOnboarding, key)
                    if onboard and onboard.manager_email and manager and onboard.manager_email != manager:
                        raise WorkflowError('This pair has a different monitor. Reassign it explicitly.')
                    # Keep native resolution and prior invitation evidence.
                    changed = [m['role'] != previous.get('role', 'participant')
                               for m, previous in zip(members, reg.members)]
                    if any(changed):
                        for person, previous in zip(members, reg.members):
                            if person['role'] == previous.get('role', 'participant'):
                                continue
                            prior = s.scalar(select(SharedInvitation).where(
                                SharedInvitation.email == person['email'],
                                SharedInvitation.status.in_(('pending', 'accepted', 'unknown'))
                            ))
                            if prior:
                                raise WorkflowError(
                                    f"{person['email']}: native invitation already exists. "
                                    'Change the native team role with explicit Admin review, '
                                    'then import the updated roster.'
                                )
                    for person, previous in zip(members, reg.members):
                        if (person['login'] and previous.get('login') and
                                person['login'] != previous.get('login')):
                            pending = s.scalar(select(SharedInvitation).where(
                                SharedInvitation.email == person['email'],
                                SharedInvitation.status.in_(('pending', 'accepted', 'unknown'))
                            ))
                            if pending:
                                raise WorkflowError(
                                    f"{person['email']}: native account login is already "
                                    'linked to an invitation. Review identity before changing.'
                                )
                        previous['role'] = person['role']
                        if person['login']:
                            previous['login'] = person['login']
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(reg, 'members')
                    if onboard and manager and not onboard.manager_email:
                        onboard.manager_email = manager
                else:
                    s.add(Registration(id=key, team_name=name, members=members))
                    s.flush()
                    s.add(TeamOnboarding(registration_id=key, manager_email=manager))

            # Compute one requested native role per email across all pairs.
            requested = {}
            for reg in s.scalars(select(Registration)):
                for person in reg.members:
                    email = person['email']
                    role = person.get('role', 'participant')
                    entry = requested.setdefault(email, {
                        'roles': set(), 'logins': set(), 'registration_ids': set(),
                    })
                    entry['roles'].add(role)
                    if person.get('login'):
                        entry['logins'].add(person['login'])
                    entry['registration_ids'].add(reg.id)
                onboard = s.get(TeamOnboarding, reg.id)
                if onboard and onboard.manager_email:
                    email = onboard.manager_email
                    entry = requested.setdefault(email, {
                        'roles': set(), 'logins': set(), 'registration_ids': set(),
                    })
                    entry['roles'].add('monitor')
                    entry['registration_ids'].add(reg.id)
            for key, name, members, manager, login in rows:
                if manager and login:
                    requested[manager]['logins'].add(login)
            for email, data in requested.items():
                roles = data['roles']
                if 'monitor' in roles and 'participant' in roles:
                    raise WorkflowError(
                        f'{email}: use participant_monitor to declare both roles.')
                if len(data['logins']) > 1:
                    raise WorkflowError(
                        f'{email}: conflicting Supervisely logins in the CSV.')
                event_role = (
                    'participant_monitor'
                    if 'participant_monitor' in roles else
                    'monitor' if 'monitor' in roles else 'participant'
                )
                native_role = ('reviewer' if event_role == 'participant_monitor' else
                               'manager' if event_role == 'monitor' else 'annotator')
                current = s.get(EventInvitee, email)
                if current:
                    requested_login = next(iter(data['logins']), '')
                    if requested_login and current.login and requested_login != current.login:
                        active_login = s.scalar(select(SharedInvitation).where(
                            SharedInvitation.email == email,
                            SharedInvitation.status.in_(('pending', 'accepted', 'unknown'))
                        ))
                        if active_login:
                            raise WorkflowError(
                                f'{email}: cannot replace a native account login after '
                                'an invitation or acceptance without explicit review.'
                            )
                    if current.native_role != native_role:
                        active = s.scalar(select(SharedInvitation).where(
                            SharedInvitation.email == email,
                            SharedInvitation.status.in_(('pending', 'accepted', 'unknown'))
                        ))
                        if active:
                            raise WorkflowError(
                                f'{email}: cannot silently change an existing '
                                'invitation or accepted native role. Review it first.'
                            )
                    current.event_role = event_role
                    current.native_role = native_role
                    current.registration_ids = sorted(data['registration_ids'])
                    if data['logins']:
                        current.login = next(iter(data['logins']))
                else:
                    s.add(EventInvitee(
                        email=email,
                        login=next(iter(data['logins']), ''),
                        event_role=event_role, native_role=native_role,
                        registration_ids=sorted(data['registration_ids']),
                    ))
            s.add(Audit(
                team_id=0, batch_id='', actor_id=actor,
                action='registrations_imported',
                details={'teams': len(rows), 'declared_invitees':len(requested)},
            ))
        return len(rows)

    def rows(self):
        with self.sessions() as s:
            rows = []
            for r in s.scalars(select(Registration).order_by(Registration.team_name)):
                onboarding = s.get(TeamOnboarding, r.id)
                rows.append({'id': r.id, 'team_name': r.team_name, 'members': r.members, 'monitor_id': r.monitor_id,
                    'team_id': r.team_id, 'error': r.error,
                    'ready': onboarding.status == 'ready' if onboarding and onboarding.memberships else all(m.get('user_id') and m['status'] in ('matched', 'verified', 'accepted') for m in r.members),
                    'monitor_email': onboarding.manager_email if onboarding else '',
                    'membership_status': onboarding.status if onboarding and (onboarding.memberships or not r.team_id) else 'ready',
                    'memberships': onboarding.memberships if onboarding else [],
                    'checked_at': onboarding.checked_at if onboarding else None})
            return rows

    def resolve(self, gateway, actor, row_id=None, overrides=None):
        overrides = overrides or {}
        with self.sessions() as s:
            rows = list(s.scalars(select(Registration).where(Registration.id == row_id))) if row_id else list(s.scalars(select(Registration)))
            if row_id and not rows:
                raise WorkflowError('Registration not found.')
        profiles = gateway.visible_accounts()
        by_email = {}
        for profile in profiles:
            email = str(getattr(profile, 'email', '') or '').strip().casefold()
            if email: by_email.setdefault(email, {})[profile.id] = profile
        for row in rows:
            if row.team_id: continue
            members = []
            for i, original in enumerate(row.members):
                member = dict(original)
                login = str(overrides.get(str(i), member.get('login', ''))).strip()
                matches = list(by_email.get(member['email'], {}).values())
                if login:
                    profile = gateway.lookup_login(login)
                    returned_email = str(getattr(profile, 'email', '') or '').strip().casefold() if profile else ''
                    manual = profile and not returned_email and overrides.get(f'verified_{i}') is True
                    matches = [profile] if profile and (returned_email == member['email'] or manual) else []
                if len(matches) == 1 and not getattr(matches[0], 'disabled', False):
                    person = matches[0]
                    hidden = not getattr(person, 'email', None)
                    member.update(user_id=person.id, login=person.login, name=person.name or person.login, status='verified' if hidden else 'matched')
                else:
                    member.update(user_id=None, login=login, status='ambiguous' if len(matches) > 1 else 'unresolved')
                members.append(member)
            ids = [m['user_id'] for m in members if m['user_id']]
            if len(ids) != len(set(ids)):
                raise WorkflowError('Both registration emails resolved to the same Supervisely account.')
            with self.sessions.begin() as s:
                current = s.get(Registration, row.id)
                if not current.team_id: current.members, current.error = members, ''
                s.add(Audit(team_id=0, batch_id='', actor_id=actor, action='registration_matched',
                            details={'registration_id': row.id, 'members': members}))
        return self.rows()

    def provision(self, gateway, setup, actor, row_id, monitor_id=0):
        with self.sessions() as s:
            row = s.get(Registration, row_id)
            if not row: raise WorkflowError('Registration not found.')
            if row.team_id: return row.team_id
            if not all(m.get('user_id') and m['status'] in ('matched', 'verified') for m in row.members):
                raise WorkflowError('Resolve both participant emails to verified accounts first.')
            # Adopt only a complete previously registered matching pair (e.g. retry after process interruption).
            matching = list(s.scalars(select(Team).where(Team.name == row.team_name)))
            if matching:
                if set(matching[0].annotator_ids) != {m['user_id'] for m in row.members}:
                    raise WorkflowError('A different participant pair already uses this team name.')
                tid = matching[0].id
            else: tid = None
        if tid is None:
            from uuid import uuid4
            try:
                tid = setup.register_team(gateway, actor, uuid4().hex, row.team_name,
                    [m['login'] for m in row.members], monitor_id or None)
            except WorkflowError as e:
                with self.sessions.begin() as s: s.get(Registration, row_id).error = str(e)
                raise
        with self.sessions.begin() as s:
            current = s.get(Registration, row_id)
            current.team_id, current.monitor_id, current.error = tid, int(monitor_id or 0), ''
        return tid
