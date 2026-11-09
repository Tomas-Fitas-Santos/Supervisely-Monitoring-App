"""Two-sheet DEMO/PRODUCTION roster: lossless source row storage and reviewed roles.

Native invitation writes are deliberately separate. Incomplete/cancelled or
consent-restricted participants remain visible to organisers but never become
automatically inviteable competition pairs.
"""
import base64
import binascii
import csv
import hashlib
import io
from collections import defaultdict
from datetime import date, datetime
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from sqlalchemy import select

from .db import (Audit, EventConfig, EventInvitee, EventRosterRecord,
                 SharedInvitation, now)
from .service import WorkflowError

MAX_SIZE = 3 * 1024 * 1024
MAX_UNPACKED = 24 * 1024 * 1024
ROLES = {'monitor', 'participant', 'participant_monitor'}


def _string(value):
    if value is None:
        return ''
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value).strip()


def _email(value):
    email = _string(value).casefold()
    return email if (email.count('@') == 1 and
                     all(email.split('@', 1)) and
                     len(email) <= 254 and
                     not any(c.isspace() for c in email)) else ''


def _boolean_yes(value):
    return _string(value).casefold() in ('sim', 'yes', 'true', '1')


def _cancelled(fields):
    status = fields.get('registration_status', '')
    confirmation = fields.get('presence_confirmation', '')
    return ('cancel' in (status + ' ' + confirmation).casefold()
            or 'desist' in (status + ' ' + confirmation).casefold())


class EventPeople:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _decode_workbook(encoded):
        if not isinstance(encoded, str) or len(encoded) > MAX_SIZE * 4 // 3 + 16:
            raise WorkflowError('Choose a roster Excel workbook below 3 MiB.')
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise WorkflowError('The roster workbook was not encoded correctly.') from None
        if not payload or len(payload) > MAX_SIZE:
            raise WorkflowError('Choose a roster Excel workbook below 3 MiB.')
        try:
            with ZipFile(io.BytesIO(payload)) as archive:
                if (len(archive.infolist()) > 4000 or
                    sum(f.file_size for f in archive.infolist()) > MAX_UNPACKED):
                    raise WorkflowError('Roster workbook contains too much data.')
            workbook = load_workbook(io.BytesIO(payload), read_only=True,
                                     data_only=True, keep_links=False)
        except WorkflowError:
            raise
        except (BadZipFile, ValueError, OSError, KeyError):
            raise WorkflowError('Cannot read the uploaded Excel workbook.') from None
        try:
            if set(workbook.sheetnames) != {'Monitors', 'Participants'}:
                raise WorkflowError('Excel roster needs exactly Monitors and Participants tabs.')
            parsed = []
            for tab in ('Monitors', 'Participants'):
                sheet = workbook[tab]
                if sheet.max_row > 10001 or sheet.max_column > 60:
                    raise WorkflowError('Roster workbook is larger than permitted.')
                iterator = sheet.iter_rows(values_only=True)
                headers = [_string(v) for v in next(iterator, [])]
                headers = [h for h in headers if h]
                required = ({'monitor_email', 'event_role'} if tab == 'Monitors' else
                            {'team_name', 'participant_name', 'participant_email',
                             'participant_role'})
                if (not required.issubset(set(headers)) or
                    len(headers) != len(set(headers))):
                    raise WorkflowError(tab + ' tab has missing or duplicate column headers.')
                for number, cells in enumerate(iterator, 2):
                    fields = {h: _string(cells[i] if i < len(cells) else None)
                              for i, h in enumerate(headers)}
                    if not any(fields.values()):
                        continue
                    parsed.append((tab, number, fields))
            return parsed
        finally:
            workbook.close()

    @staticmethod
    def _source(tab, number, fields):
        email_column = 'monitor_email' if tab == 'Monitors' else 'participant_email'
        name_column = 'monitor_name' if tab == 'Monitors' else 'participant_name'
        role_column = 'event_role' if tab == 'Monitors' else 'participant_role'
        role = fields.get(role_column, '').strip().casefold()
        if role not in ROLES:
            raise WorkflowError(f'{tab} row {number}: invalid role {role!r}.')
        email = _email(fields.get(email_column, ''))
        if tab == 'Monitors' and not email:
            raise WorkflowError(f'Monitors row {number}: a valid email is required.')
        team = fields.get('team_name', '') if tab == 'Participants' else ''
        team_no = fields.get('team_number', '') if tab == 'Participants' else ''
        name = fields.get(name_column, '')
        identity = (email if email else f'{team_no}:{team}:{name}:{number}')
        record_id = hashlib.sha256((tab + ':' + identity).encode()).hexdigest()
        needs_review = (tab == 'Participants' and
                        (not email or not team or _cancelled(fields) or
                         not _boolean_yes(fields.get('data_processing_consent', ''))))
        return {
            'record_id': record_id, 'source_sheet': tab,
            'team_number': team_no, 'team_name': team,
            'display_name': name, 'email': email,
            'event_role': role, 'source_fields': fields,
            'needs_review': needs_review,
        }

    def import_workbook(self, actor, encoded):
        source = [self._source(*item) for item in self._decode_workbook(encoded)]
        duplicate = defaultdict(set)
        for person in source:
            if person['email']:
                duplicate[person['email']].add(person['source_sheet'])
        conflicts = {email for email, sheets in duplicate.items() if len(sheets) > 1}
        for person in source:
            if person['email'] in conflicts:
                person['needs_review'] = True

        with self.sessions.begin() as session:
            config = session.get(EventConfig, 1)
            # Re-import is authoritative for the external monitor allowlist:
            # identities absent from the new sheet lose login permission.
            current_ids = {item['record_id'] for item in source
                           if item['source_sheet'] == 'Monitors'}
            for old in session.scalars(select(EventRosterRecord).where(
                    EventRosterRecord.source_sheet == 'Monitors')):
                if old.record_id not in current_ids:
                    old.event_role = 'inactive'
                    old.needs_review = True
            for item in source:
                previous = session.get(EventRosterRecord, item['record_id'])
                if previous:
                    # Never discard an explicit role adjustment from the app.
                    item['event_role'] = previous.event_role
                    previous.source_sheet = item['source_sheet']
                    previous.team_number = item['team_number']
                    previous.team_name = item['team_name']
                    previous.display_name = item['display_name']
                    previous.email = item['email']
                    previous.source_fields = item['source_fields']
                    previous.needs_review = item['needs_review']
                    previous.updated_at = now()
                else:
                    session.add(EventRosterRecord(**item))
                if item['source_sheet'] != 'Monitors':
                    continue
                email = item['email']
                existing = session.get(EventInvitee, email)
                if email == _email(config.owner_login or ''):
                    # Login is not necessarily email; native owner permissions are
                    # verified elsewhere. This file never grants native Admin.
                    pass
                supplied_login = _string(item['source_fields'].get('supervisely_login', ''))
                if len(supplied_login) > 180:
                    raise WorkflowError('Supervisely account login exceeds 180 characters.')
                if existing:
                    if supplied_login and supplied_login != existing.login:
                        pending_login = session.scalar(select(SharedInvitation).where(
                            SharedInvitation.email == email,
                            SharedInvitation.status.in_(('pending','accepted','unknown'))))
                        if pending_login:
                            raise WorkflowError(
                                'The monitor login is linked to a native invitation. '
                                'Review membership before changing it.')
                        existing.login = supplied_login
                    if existing.event_role != item['event_role']:
                        native = session.scalar(select(SharedInvitation).where(
                            SharedInvitation.email == email,
                            SharedInvitation.status.in_(('pending', 'accepted', 'unknown'))
                        ))
                        if native:
                            raise WorkflowError('An already invited monitor has a different role. '
                                                'Review native permissions before re-importing.')
                        existing.event_role = item['event_role']
                        existing.native_role = (
                            'reviewer' if item['event_role'] == 'participant_monitor'
                            else 'manager' if item['event_role'] == 'monitor' else 'annotator')
                else:
                    session.add(EventInvitee(
                        email=email, event_role=item['event_role'],
                        native_role=('reviewer' if item['event_role'] == 'participant_monitor'
                                     else 'manager' if item['event_role'] == 'monitor'
                                     else 'annotator'), registration_ids=[], login=supplied_login))
            session.add(Audit(
                team_id=0, batch_id='', actor_id=int(actor),
                action='roster_workbook_imported',
                details={'monitors':sum(v['source_sheet']=='Monitors' for v in source),
                         'participants':sum(v['source_sheet']=='Participants' for v in source),
                         'review_required':sum(v['needs_review'] for v in source),
                         'email_overlap':len(conflicts)}))
        return {'monitors':sum(v['source_sheet']=='Monitors' for v in source),
                'participants':sum(v['source_sheet']=='Participants' for v in source),
                'needs_review':sum(v['needs_review'] for v in source),
                'email_overlap':len(conflicts)}

    def overview(self):
        with self.sessions() as session:
            invitations = {inv.email:inv.status for inv in session.scalars(select(SharedInvitation))}
            return [{
                'id':p.record_id, 'source_sheet':p.source_sheet,
                'team_number':p.team_number, 'team_name':p.team_name,
                'display_name':p.display_name, 'email':p.email,
                'event_role':p.event_role, 'needs_review':p.needs_review,
                'source_fields':p.source_fields,
                'invitation_status':invitations.get(p.email, 'not_invited')
            } for p in session.scalars(select(EventRosterRecord).order_by(
                EventRosterRecord.source_sheet, EventRosterRecord.team_number,
                EventRosterRecord.display_name))]

    def set_login(self, actor, record_id, login):
        """Set an explicitly supplied native login before sending invitations."""
        from .db import Registration
        from sqlalchemy.orm.attributes import flag_modified
        login = _string(login)
        if not login or len(login) > 180:
            raise WorkflowError('Enter a valid Supervisely login, at most 180 characters.')
        with self.sessions.begin() as session:
            row = session.get(EventRosterRecord, str(record_id or ''))
            if not row or not row.email:
                raise WorkflowError('Choose a roster account with a valid email.')
            active = session.scalar(select(SharedInvitation).where(
                SharedInvitation.email == row.email,
                SharedInvitation.status.in_(('pending','accepted','unknown'))))
            if active:
                raise WorkflowError('This native identity is already invited. '
                                    'Verify the real account before changing its login.')
            all_records = session.scalars(select(EventRosterRecord).where(
                EventRosterRecord.email == row.email))
            for person in all_records:
                person.source_fields = {**person.source_fields, 'supervisely_login': login}
            invitee = session.get(EventInvitee, row.email)
            if invitee:
                invitee.login = login
            for reg in session.scalars(select(Registration)):
                changed=False
                for member in reg.members or []:
                    if str(member.get('email') or '').casefold() == row.email:
                        member['login'] = login
                        changed=True
                if changed:
                    flag_modified(reg, 'members')
            session.add(Audit(team_id=0,batch_id='',actor_id=int(actor),
                action='supervisely_login_edited',
                details={'email':row.email,'login':login}))
        return True

    def set_role(self, actor, record_id, event_role):
        if event_role not in ROLES:
            raise WorkflowError('Choose monitor, participant or participant_monitor.')
        with self.sessions.begin() as session:
            row = session.get(EventRosterRecord, str(record_id or ''))
            if not row:
                raise WorkflowError('Roster person was not found.')
            if not row.email:
                raise WorkflowError('A valid participant email is required to assign a native role.')
            active = session.scalar(select(SharedInvitation).where(
                SharedInvitation.email == row.email,
                SharedInvitation.status.in_(('pending','accepted','unknown'))
            ))
            if active:
                raise WorkflowError('This person already has a pending/accepted native role. '
                                    'Change native Supervisely role with Admin review first.')
            # The same email can be present in both tabs, but its role must
            # remain consistent across all local records and the native invitation.
            for linked in session.scalars(select(EventRosterRecord).where(
                    EventRosterRecord.email == row.email)):
                linked.event_role = event_role
                if event_role == 'participant_monitor':
                    linked.needs_review = (
                        not linked.email or _cancelled(linked.source_fields) or
                        (linked.source_sheet == 'Participants' and
                         not _boolean_yes(linked.source_fields.get('data_processing_consent', '')))
                    )
            invited = session.get(EventInvitee, row.email)
            if invited:
                invited.event_role = event_role
                invited.native_role = (
                    'reviewer' if event_role == 'participant_monitor' else
                    'manager' if event_role == 'monitor' else 'annotator')
            else:
                session.add(EventInvitee(email=row.email, event_role=event_role,
                    native_role='reviewer' if event_role == 'participant_monitor'
                    else 'manager' if event_role == 'monitor' else 'annotator',
                    registration_ids=[], login=''))
            from .db import Registration
            from sqlalchemy.orm.attributes import flag_modified
            for reg in session.scalars(select(Registration)):
                changed = False
                for member in reg.members or []:
                    if str(member.get('email') or '').casefold() == row.email:
                        member['role'] = event_role
                        changed = True
                if changed:
                    flag_modified(reg,'members')
            session.add(Audit(team_id=0,batch_id='',actor_id=int(actor),
                action='event_role_edited',details={'email':row.email, 'role':event_role}))
        return True

    def change_native_role(self, gateway, actor, record_id, event_role, confirmed=False):
        """Admin-approved role reconciliation for an ALREADY accepted native member.

        A successful native write is marked unknown until read-only refresh
        verifies the effective role. Never edit the event owner Admin account.
        """
        from .db import EventMember, Registration
        from .setup_gateway import SetupGateway
        from sqlalchemy.orm.attributes import flag_modified
        if not confirmed or event_role not in ROLES:
            raise WorkflowError('Confirm the real Supervisely role change explicitly.')
        with self.sessions() as session:
            person = session.get(EventRosterRecord, str(record_id or ''))
            if not person or not person.email:
                raise WorkflowError('Choose a roster person with a valid email.')
            config = session.get(EventConfig, 1)
            if not config or not config.monitoring_team_id:
                raise WorkflowError('Configure the existing Supervisely event team first.')
            invitation = session.get(SharedInvitation,
                                     (config.monitoring_team_id, person.email))
            if not invitation or invitation.status != 'accepted' or not invitation.user_id:
                raise WorkflowError('A native role can change only for an accepted, verified member.')
            if int(invitation.user_id) == int(config.owner_id or -1):
                raise WorkflowError('The event owner must retain Supervisely Admin permissions.')
            tid, member_id, email = (int(config.monitoring_team_id),
                                     int(invitation.user_id), person.email)
        role = ('reviewer' if event_role == 'participant_monitor' else
                'manager' if event_role == 'monitor' else 'annotator')
        # The SDK call itself is the only remote mutation. If uncertain, inspect
        # live team membership rather than blindly retrying.
        setup_gateway = SetupGateway(gateway.api)
        role_id = setup_gateway.role_id(role)
        try:
            current = gateway.api.user.get_member_info_by_id(tid, member_id)
            if not current:
                raise WorkflowError('Native account membership must be verified before changing roles.')
            if current.role == 'admin':
                raise WorkflowError('Native Admin roles require manual owner review.')
            if current.role != role:
                gateway.api.user.change_team_role(member_id, tid, role_id)
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError('Native role change returned an uncertain result. '
                                'Refresh real Supervisely memberships before trying again.') from None
        with self.sessions.begin() as session:
            invite = session.get(SharedInvitation, (tid,email))
            if not invite or invite.user_id != member_id:
                raise WorkflowError('Native role may have changed. Refresh membership before continuing.')
            invite.status = 'unknown'
            invite.checked_at = None
            person_entries = session.scalars(select(EventRosterRecord).where(
                EventRosterRecord.email == email))
            for entry in person_entries:
                entry.event_role = event_role
            expected = session.get(EventInvitee, email)
            if expected:
                expected.event_role = event_role
                expected.native_role = role
            for reg in session.scalars(select(Registration)):
                changed = False
                for m in reg.members or []:
                    if str(m.get('email') or '').casefold() == email:
                        m['role'] = event_role
                        m['status'] = 'unresolved'
                        changed = True
                if changed:
                    flag_modified(reg,'members')
            # Prevent stale monitoring access until the native role is reconciled.
            member = session.get(EventMember, member_id)
            if member and event_role == 'participant':
                member.group = 'annotators'
            session.add(Audit(team_id=0,batch_id='',actor_id=int(actor),
                action='native_role_change_requested',
                details={'email':email, 'user_id':member_id,
                         'requested_role':role, 'event_role':event_role}))
        return True

    def prepare_pairs(self, actor):
        """Save complete eligible competition pairs, without sending invitations."""
        from .registration import COLUMNS, Registrations
        people = [p for p in self.overview()
                  if p['source_sheet']=='Participants' and not p['needs_review']]
        grouped = defaultdict(list)
        for person in people:
            grouped[person['team_name'].casefold()].append(person)
        valid = []
        for _, members in grouped.items():
            if (len(members) != 2 or
                any(m['event_role']=='monitor' for m in members) or
                len({m['email'] for m in members}) != 2):
                continue
            valid.append(members)
        if not valid:
            raise WorkflowError('No two-person eligible teams yet. Resolve roster warnings first.')
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for members in valid:
            writer.writerow({
                'team_name':members[0]['team_name'],
                'member_1_email':members[0]['email'],
                'member_2_email':members[1]['email'],
                'member_1_name':members[0]['display_name'],
                'member_2_name':members[1]['display_name'],
                'member_1_role':members[0]['event_role'],
                'member_2_role':members[1]['event_role'],
                'member_1_login':members[0]['source_fields'].get('supervisely_login',''),
                'member_2_login':members[1]['source_fields'].get('supervisely_login',''),
            })
        return Registrations(self.sessions).import_csv(
            actor, stream.getvalue(), {h:h for h in COLUMNS})

    def prepare_demo_pair(self, actor, emails):
        """Use two supplied staff emails for a dual-role rehearsal, never synthetic identities."""
        from .registration import COLUMNS, Registrations
        if not isinstance(emails, list) or len(emails)!=2 or emails[0]==emails[1]:
            raise WorkflowError('Choose exactly two different DEMO participants.')
        with self.sessions() as session:
            found = {p.email:p for p in session.scalars(select(EventRosterRecord))}
        selected=[]
        for address in emails:
            person=found.get(_email(address))
            if not person or person.event_role!='participant_monitor':
                raise WorkflowError('Both selected DEMO users must first be participant_monitor.')
            selected.append(person)
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow({
            'team_name':'DEMO Trial Pair',
            'member_1_email':selected[0].email,'member_2_email':selected[1].email,
            'member_1_name':selected[0].display_name or selected[0].email,
            'member_2_name':selected[1].display_name or selected[1].email,
            'member_1_role':'participant_monitor',
            'member_2_role':'participant_monitor',
        })
        return Registrations(self.sessions).import_csv(
            actor,stream.getvalue(),{h:h for h in COLUMNS})
