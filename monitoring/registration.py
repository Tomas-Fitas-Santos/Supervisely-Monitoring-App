"""CSV column mapping, exact email matching and idempotent pair provisioning."""
import csv
import hashlib
import io
from sqlalchemy import select
from .db import Audit, Registration, Team, TeamOnboarding
from .service import WorkflowError

COLUMNS = ['team_name', 'member_1_email', 'member_2_email', 'member_1_name', 'member_2_name', 'member_1_login', 'member_2_login', 'monitor_email']
PERSON_COLUMNS = ['team_name', 'participant_email', 'participant_name', 'participant_login']


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
        headers, raw = self.columns(text)
        if not isinstance(mapping, dict) or set(mapping) - set(COLUMNS + PERSON_COLUMNS):
            raise WorkflowError('Invalid CSV column mapping.')
        if mapping.get('participant_email'):
            if mapping.get('team_name') not in headers or mapping['participant_email'] not in headers:
                raise WorkflowError('Map the team and participant email columns.')
            grouped = {}
            for row in raw:
                name = row.get(mapping['team_name'], '').strip()
                grouped.setdefault(name, []).append(row)
            converted = []
            for name, people in grouped.items():
                if len(people) != 2:
                    raise WorkflowError(f'Team {name or "(unnamed)"} must have exactly two participant rows.')
                item = {'team_name': name}
                for i, person in enumerate(people, 1):
                    for field in ('email', 'name', 'login'):
                        item[f'member_{i}_{field}'] = person.get(mapping.get(f'participant_{field}', ''), '')
                converted.append(item)
            raw, headers, mapping = converted, COLUMNS, {field:field for field in COLUMNS}
        for field in COLUMNS[:2]:
            if mapping.get(field) not in headers:
                raise WorkflowError('Map the team name and first participant email column; the second participant is optional.')
        mapped_required = [mapping[field] for field in COLUMNS[:3] if mapping.get(field)]
        if len(set(mapped_required)) != len(mapped_required):
            raise WorkflowError('Map different columns for team name and the two emails.')
        rows, seen, emails = [], set(), set()
        for number, raw_row in enumerate(raw, 2):
            row = {field: str(raw_row.get(mapping.get(field, ''), '')).strip() for field in COLUMNS}
            name = row['team_name']
            if not name or len(name) > 180 or name.casefold() in seen:
                raise WorkflowError(f'Row {number}: missing or duplicate team name.')
            seen.add(name.casefold())
            members = []
            for i in (1, 2):
                email = row[f'member_{i}_email'].casefold()
                if i == 2 and not email:
                    continue
                if len(email) > 254 or email.count('@') != 1 or not email.split('@', 1)[0] or not email.rsplit('@', 1)[1] or any(c.isspace() for c in email) or email in emails:
                    raise WorkflowError(f'Row {number}: invalid email or a participant appears in multiple teams.')
                emails.add(email)
                members.append({'email': email, 'name': row[f'member_{i}_name'],
                                'login': row[f'member_{i}_login'], 'user_id': None, 'status': 'unresolved'})
            key = hashlib.sha256(name.casefold().encode()).hexdigest()
            manager = row['monitor_email'].casefold()
            if manager and (manager.count('@') != 1 or any(c.isspace() for c in manager) or not all(manager.split('@'))
                            or len(manager) > 254 or manager in {m['email'] for m in members}):
                raise WorkflowError(f'Row {number}: use a valid monitor email distinct from the annotators.')
            rows.append((key, name, members, manager))
        if {manager for _, _, _, manager in rows if manager}.intersection(emails):
            raise WorkflowError('A monitor cannot also be registered as an annotator in this event.')
        with self.sessions.begin() as s:
            existing = list(s.scalars(select(Registration)))
            existing_participants = {m['email'] for r in existing for m in r.members}
            existing_managers = {p.manager_email for p in s.scalars(select(TeamOnboarding)) if p.manager_email}
            if existing_managers.intersection(emails) or existing_participants.intersection(manager for _,_,_,manager in rows if manager):
                raise WorkflowError('Monitors and annotators must use separate event accounts.')
            for key, name, members, manager in rows:
                for previous in existing:
                    if previous.id != key and {m['email'] for m in previous.members}.intersection(m['email'] for m in members):
                        raise WorkflowError('An email is already registered to another event team.')
                record = s.get(Registration, key)
                if record:
                    if [m['email'] for m in record.members] != [m['email'] for m in members]:
                        raise WorkflowError('This team name has different registered members. Resolve the roster explicitly.')
                    onboarding = s.get(TeamOnboarding, key)
                    if manager and onboarding and not record.team_id and onboarding.manager_email != manager:
                        raise WorkflowError('This registration has a different monitor email. Use the explicit monitor assignment controls.')
                    continue  # Idempotent import preserves resolution and team ownership.
                s.add(Registration(id=key, team_name=name, members=members))
                s.flush()
                s.add(TeamOnboarding(registration_id=key, manager_email=manager))
            s.add(Audit(team_id=0, batch_id='', actor_id=actor, action='registrations_imported', details={'teams': len(rows)}))
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
