"""Read the supplied people rosters directly, with no column-matching UI."""
import csv
import hashlib
import io
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import delete, select

from .db import EventConfig, EventInvitee, RosterContact, SharedInvitation
from .registration import COLUMNS, Registrations, participant_role
from .service import WorkflowError
from .shared_invitations import normal_email


def header_key(value):
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]', '', text)


ALIASES = {header_key(field): field for field in COLUMNS + [
    'team_number', 'participant_email', 'participant_name', 'participant_login',
    'participant_role', 'monitor_name', 'event_role', 'source_row',
]}
ALIASES.update({
    'team': 'team_name', 'equipa': 'team_name', 'nomedaequipa': 'team_name',
    'teamid': 'team_number', 'numerodaequipa': 'team_number',
    'email': 'participant_email', 'emailaddress': 'participant_email',
    'name': 'participant_name', 'nome': 'participant_name',
    'superviselylogin': 'participant_login', 'login': 'participant_login',
    'role': 'participant_role', 'firstemail': 'member_1_email',
    'secondemail': 'member_2_email', 'firstname': 'member_1_name',
    'secondname': 'member_2_name',
})


def table_rows(text):
    headers, raw = Registrations.columns(text)
    mapping = {header: ALIASES[header_key(header)] for header in headers if header_key(header) in ALIASES}
    if len(set(mapping.values())) != len(mapping):
        raise WorkflowError('The file has duplicate columns for the same person detail.')
    if not set(mapping.values()).intersection(('member_1_email', 'participant_email', 'monitor_email')):
        raise WorkflowError('No participant or monitor email column was found in this roster.')
    return [{mapping[key]: str(value or '').strip() for key, value in row.items() if key in mapping}
            for row in raw]


def import_roster(sessions, actor, tables):
    """Import all worksheets in one transaction; retain incomplete registrations."""
    contacts, groups, pair_rows = [], defaultdict(list), []
    for label, text in tables:
        rows = table_rows(text)
        for index, row in enumerate(rows, 2):
            if row.get('member_1_email') or 'member_1_email' in row:
                pair_rows.append(row)
                for member in (1, 2):
                    if row.get(f'member_{member}_email') or row.get(f'member_{member}_name'):
                        contacts.append(dict(email=row.get(f'member_{member}_email', ''),
                            name=row.get(f'member_{member}_name', ''),
                            role=participant_role(row.get(f'member_{member}_role')),
                            team_name=row.get('team_name', ''), source=f'{label}:{index}:{member}'))
                if row.get('monitor_email'):
                    contacts.append(dict(email=row['monitor_email'], name=row.get('monitor_name', ''),
                        role='monitor', login=row.get('monitor_login', ''), team_name=row.get('team_name', ''), source=f'{label}:{index}:monitor'))
                continue
            staff = ('participant_email' not in row or
                     row.get('event_role', row.get('participant_role', '')).lower() == 'monitor')
            if staff:
                email = row.get('monitor_email') or row.get('participant_email', '')
                name = row.get('monitor_name') or row.get('participant_name', '')
                if not email and not name:
                    continue
                contacts.append(dict(email=email, name=name, role='monitor',
                    login=row.get('monitor_login') or row.get('participant_login', ''),
                    team_name='', source=f'{label}:{index}'))
            else:
                if not row.get('participant_email') and not row.get('participant_name'):
                    continue
                name = row.get('team_name', '')
                number = row.get('team_number', '')
                team = f'{number} · {name}' if number else name
                if not team:
                    raise WorkflowError(f'Row {index}: a team name or number is missing.')
                row['team_name'] = team
                row['participant_role'] = participant_role(row.get('participant_role'))
                groups[team].append(row)
                contacts.append(dict(email=row.get('participant_email', ''), name=row.get('participant_name', ''),
                    role=row['participant_role'], login=row.get('participant_login', ''),
                    team_name=team, source=f'{label}:{row.get("source_row") or index}'))
                if row.get('monitor_email'):
                    contacts.append(dict(email=row['monitor_email'], name=row.get('monitor_name', ''),
                        role='monitor', login=row.get('monitor_login', ''), team_name=team, source=f'{label}:{index}:monitor'))
    if not contacts:
        raise WorkflowError('This file contains no monitors or participants.')
    monitor_emails = {p['email'].lower() for p in contacts if p['role'] == 'monitor' and p['email']}
    if not any(p['role'] == 'monitor' for p in contacts):
        # Separate CSV uploads describe the same event as the two-sheet workbook.
        with sessions() as session:
            monitor_emails.update(session.scalars(select(EventInvitee.email).where(
                EventInvitee.event_role.in_(('monitor', 'participant_monitor')))))
    for team, people in groups.items():
        if len(people) > 2:
            raise WorkflowError(f'{team}: more than two participants share this team. Add a distinct team_number for each pair.')
        row = {'team_name': team}
        for field in ('monitor_email', 'monitor_login'):
            values = {p.get(field, '').casefold() if field == 'monitor_email' else p.get(field, '') for p in people}
            values.discard('')
            if len(values) > 1:
                raise WorkflowError(f'{team}: conflicting monitor details.')
            row[field] = next(iter(values), '')
        for index, person in enumerate(people, 1):
            for field in ('email', 'name', 'login', 'role'):
                row[f'member_{index}_{field}'] = person.get(f'participant_{field}', '')
        pair_rows.append(row)
    for row in pair_rows:
        for index in (1, 2):
            if row.get(f'member_{index}_email', '').lower() in monitor_emails:
                row[f'member_{index}_role'] = 'participant_monitor'
    # Email columns are the identities; spreadsheet invitation/check-in/native
    # role columns cannot authenticate people or grant native Admin access.
    for person in contacts:
        if person['email']:
            person['email'] = normal_email(person['email'])
    with sessions.begin() as session:
        if pair_rows:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction='ignore')
            writer.writeheader()
            for row in pair_rows:
                # Empty optional role cells retain the ordinary participant role.
                for index in (1, 2):
                    row[f'member_{index}_role'] = row.get(f'member_{index}_role') or 'participant'
                writer.writerow(row)
            Registrations(sessions).import_csv(actor, stream.getvalue(), {key:key for key in COLUMNS},
                                               session=session, allow_incomplete=True)
        tid = session.get(EventConfig, 1).monitoring_team_id
        invitees = {p.email:p for p in session.scalars(select(EventInvitee))}
        for person in contacts:
            if not person['email'] or person['role'] != 'monitor':
                continue
            current = invitees.get(person['email'])
            role = 'participant_monitor' if current and current.event_role in ('participant', 'participant_monitor') else 'monitor'
            native = 'reviewer' if role == 'participant_monitor' else 'manager'
            if current and (current.native_role != native or
                            (person.get('login') and current.login and current.login != person['login'])):
                prior = session.get(SharedInvitation, (tid, person['email']))
                if prior and prior.status not in ('not_invited', 'invitation_error'):
                    raise WorkflowError(f'{person["email"]}: review the existing native role or login before changing this roster.')
            if not current:
                current = EventInvitee(email=person['email'], event_role=role, native_role=native,
                                       login=person.get('login', ''), registration_ids=[])
                session.add(current)
                invitees[person['email']] = current
            else:
                current.event_role, current.native_role = role, native
                if person.get('login'):
                    current.login = person['login']
        kinds = {p['role'] for p in contacts}
        session.execute(delete(RosterContact).where(RosterContact.role.in_(kinds)))
        for person in contacts:
            key = hashlib.sha256(f'{person["source"]}:{person["role"]}:{person["team_name"]}'.encode()).hexdigest()
            session.merge(RosterContact(id=key, email=person['email'], name=person['name'],
                                        role=person['role'], team_name=person['team_name']))
    unique = {(p['role'], p['email'] or p['source']) for p in contacts}
    return {'teams': len(pair_rows),
            'monitors': sum(role == 'monitor' for role, _ in unique),
            'participants': sum(role != 'monitor' for role, _ in unique),
            'missing_emails': sum(not p['email'] for p in contacts)}
