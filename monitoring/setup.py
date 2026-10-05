"""Durable setup plans, upload staging, and serialized remote provisioning."""
import base64
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from .db import Audit, Batch, EventConfig, EventMember, SetupOperation, SetupState, Team, Upload
from .planner import allocate
from .service import WorkflowError


KEY = re.compile(r'^[a-zA-Z0-9_-]{8,80}$')
EXTENSIONS = {'images': {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'},
              'video': {'.mp4', '.avi', '.mov', '.mkv', '.webm'}}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def positive(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise WorkflowError(f'{label} must be a positive integer.') from None
    if result <= 0 or str(value).strip() != str(result):
        raise WorkflowError(f'{label} must be a positive integer.')
    return result


def pilot_meta():
    """Editable pilot schema; event-specific metadata may be supplied instead."""
    import supervisely as sly
    landmarks = ['crown', 'left_eye', 'right_eye', 'beak']
    classes = sly.ObjClassCollection([sly.ObjClass('bird', sly.Rectangle, color=[32, 160, 255])]
        + [sly.ObjClass(name, sly.Point, color=color) for name, color in zip(landmarks,
            [[255, 73, 73], [19, 206, 102], [247, 186, 42], [132, 146, 166]])])
    tags = sly.TagMetaCollection([
        sly.TagMeta('bird_id', sly.TagValueType.ANY_STRING, color=[132, 146, 166], applicable_to='objectsOnly'),
        sly.TagMeta('visibility', sly.TagValueType.ONEOF_STRING, possible_values=['visible', 'occluded'],
                    color=[247, 186, 42], applicable_to='objectsOnly', applicable_classes=landmarks)])
    return sly.ProjectMeta(classes, tags).to_json()


class Setup:
    def __init__(self, sessions, monitoring_team_id, upload_dir=None):
        self.sessions = sessions
        self._legacy_monitoring_team_id = monitoring_team_id
        self.upload_dir = Path(upload_dir or os.getenv('UPLOAD_STAGING_DIR', 'data/uploads')).resolve()

    @property
    def monitoring_team_id(self):
        with self.sessions() as s:
            config = s.get(EventConfig, 1)
            if config and config.owner_id and not config.monitoring_team_id and s.scalar(select(Team.id).limit(1)) is None:
                return 0
            return (config.monitoring_team_id if config else None) or self._legacy_monitoring_team_id

    def groups(self):
        with self.sessions() as s:
            c = s.get(EventConfig, 1)
            members = [{'id': m.user_id, 'login': m.login, 'name': m.name, 'group': m.group}
                       for m in s.scalars(select(EventMember).order_by(EventMember.login))]
            return {'ready': bool(c and c.monitoring_team_id and c.annotator_team_id),
                    'monitoring_team_id': c.monitoring_team_id if c else None,
                    'annotator_team_id': c.annotator_team_id if c else None,
                    'workspace_id': c.source_workspace_id if c else None,
                    'monitors': [m for m in members if m['group'] == 'monitors'],
                    'annotators': [m for m in members if m['group'] == 'annotators']}

    def roster(self):
        with self.sessions() as s:
            return [{'id': t.id, 'name': t.name, 'monitor_id': t.monitor_id,
                     'annotator_ids': t.annotator_ids, 'revision': t.revision,
                     'has_batches': s.scalar(select(Batch.id).where(Batch.team_id == t.id).limit(1)) is not None}
                    for t in s.scalars(select(Team).order_by(Team.id))]

    def snapshot(self):
        with self.sessions() as s:
            ops = list(s.scalars(select(SetupOperation).order_by(SetupOperation.created_at.desc(),
                                                                  SetupOperation.id.desc()).limit(30)))
            lock = s.get(SetupState, 1)
            if lock and lock.operation_id and all(o.id != lock.operation_id for o in ops):
                blocked = s.get(SetupOperation, lock.operation_id)
                if blocked:
                    ops.insert(0, blocked)
            return {'roster': self.roster(), 'groups': self.groups(), 'operations': [
                {'id': o.id, 'kind': o.kind, 'state': o.state, 'actor_id': o.actor_id,
                 'result': o.result, 'created_at': o.created_at,
                 'preview': o.details.get('preview') if o.kind == 'distribution' else None} for o in ops],
                'blocked_by': lock.operation_id if lock else None}

    def directory(self):
        """Event people and assignments only; no review records or operation details."""
        groups = self.groups()
        people = {u['id']: u for u in groups['monitors'] + groups['annotators']}
        teams = self.roster()
        with self.sessions() as s:
            for team in teams:
                team['participants'] = [people.get(uid, {'id': uid, 'login': f'User {uid}', 'name': f'User {uid}'})
                                        for uid in team['annotator_ids']]
                team['can_assign'] = not bool(s.scalar(select(Batch.id).where(
                    Batch.team_id == team['id'], Batch.state != 'locked').limit(1)))
        monitors = list(groups['monitors'])
        known = {m['id'] for m in monitors}
        for uid in sorted({t['monitor_id'] for t in teams if t['monitor_id']} - known):
            monitors.append({'id': uid, 'login': f'User {uid}', 'name': f'Monitor {uid}'})
        return {'ready': groups['ready'], 'monitors': monitors, 'teams': teams,
                'annotators': groups['annotators']}

    def authorize(self, gateway, actor):
        if gateway is None:
            raise WorkflowError('Connect your Supervisely account in the app to start setup.')
        if self.monitoring_team_id:
            gateway.organiser(self.monitoring_team_id, actor)
        else:
            with self.sessions() as s:
                owner = s.get(EventConfig, 1).owner_id
            if owner != actor:
                raise WorkflowError('Only the connected organiser can create the event groups.')
        with self.sessions() as s:
            demo = s.scalar(select(Batch).where(Batch.id.like('demo-%')).limit(1))
            if demo and any(a['source_id'].startswith('demo-') for a in demo.assets):
                raise WorkflowError('This database contains synthetic demo data. Use a fresh pilot.db in DATABASE_URL and restart the app.')

    def configure_groups(self, gateway, actor, key, monitor_name, annotator_name,
                         existing_monitor_id=None, existing_annotator_id=None):
        if self.groups()['ready']:
            raise WorkflowError('Event groups have already been configured.')
        names = [str(monitor_name).strip(), str(annotator_name).strip()]
        if any(not n or len(n) > 180 for n in names) or names[0].casefold() == names[1].casefold():
            raise WorkflowError('Choose two distinct group names.')
        ids = [int(v) if v else None for v in [existing_monitor_id, existing_annotator_id]]
        if ids[0] and ids[0] == ids[1]:
            raise WorkflowError('The monitors and annotators groups must be separate teams.')
        if any(t['id'] in ids for t in self.roster()):
            raise WorkflowError('A participant-pair team cannot also be an event group.')
        for tid in ids:
            if tid:
                gateway.organiser(tid, actor)
        if self.roster() and ids[0] != self.monitoring_team_id:
            raise WorkflowError('Keep the existing monitoring team when configuring groups for an existing event.')
        # Adopt existing event participants without dropping their access during this upgrade.
        legacy_members = {}
        initial_roster = self.roster()
        with self.sessions() as s:
            owner_id = s.get(EventConfig, 1).owner_id
        for pair in initial_roster:
            for uid, group, tid in ([(u, 'annotators', pair['id']) for u in pair['annotator_ids']]
                                    + ([(pair['monitor_id'], 'monitors', self.monitoring_team_id)] if pair['monitor_id'] else [])):
                if uid in legacy_members and legacy_members[uid][0] != group:
                    raise WorkflowError('An existing account has both roles. Resolve this before creating separate groups.')
                if uid == owner_id and group == 'annotators':
                    raise WorkflowError('The monitoring organiser cannot be a participating annotator.')
                person = gateway.api.user.get_member_info_by_id(tid, uid)
                if not person or person.disabled:
                    raise WorkflowError('Restore active membership for every existing pair before creating groups.')
                legacy_members[uid] = group, person
        with self.operation(actor, key, 'groups', {'names': names, 'existing_ids': ids}) as checkpoint:
            if fingerprint(self.roster()) != fingerprint(initial_roster):
                raise WorkflowError('Pair assignments changed. Refresh setup before configuring groups.')
            with self.sessions.begin() as s:
                config = s.get(EventConfig, 1)
                if config.monitoring_team_id and config.annotator_team_id:
                    raise WorkflowError('Another organiser configured the groups first.')
                if config.owner_id is None:
                    profile = gateway.api.user.get_my_info()
                    config.owner_id, config.owner_login = actor, profile.login
                    config.server_address = gateway.api.server_address.rstrip('/')
            teams = []
            for name, tid in zip(names, ids):
                team = gateway.api.team.get_info_by_id(tid) if tid else gateway.api.team.create(
                    name, description=f'Nightjar event group. Setup {key}')
                if not team:
                    raise WorkflowError('Selected group is unavailable.')
                teams.append(team)
                checkpoint({'group': 'monitors' if len(teams) == 1 else 'annotators', 'team_id': team.id})
            workspace = gateway.api.workspace.create(teams[0].id, 'Event source data', change_name_if_conflict=True)
            checkpoint({'workspace_id': workspace.id})
            for uid, (group, person) in legacy_members.items():
                target = teams[0].id if group == 'monitors' else teams[1].id
                role = gateway.role_id('manager' if group == 'monitors' else 'annotator')
                added = gateway.add_login(target, person.login, role)
                if group == 'monitors' and added.role not in ('admin', 'manager'):
                    gateway.api.user.change_team_role(uid, target, role)
                checkpoint({'group': group, 'user_id': uid, 'login': person.login})
            with self.sessions.begin() as s:
                c = s.get(EventConfig, 1)
                c.monitoring_team_id, c.annotator_team_id = teams[0].id, teams[1].id
                c.source_workspace_id = workspace.id
                for uid, (group, person) in legacy_members.items():
                    s.add(EventMember(user_id=uid, group=group, login=person.login,
                                      name=person.name or person.login))

    def add_members(self, gateway, actor, key, group, logins):
        if group not in ('monitors', 'annotators') or not self.groups()['ready']:
            raise WorkflowError('Create the monitors and annotators groups first.')
        logins = [str(v).strip() for v in logins]
        if not logins or any(not v or len(v) > 180 for v in logins) or len({v.casefold() for v in logins}) != len(logins):
            raise WorkflowError('Enter distinct registered logins, one per line.')
        with self.sessions() as s:
            c = s.get(EventConfig, 1)
            tid = c.monitoring_team_id if group == 'monitors' else c.annotator_team_id
            if group == 'annotators' and c.owner_login and c.owner_login.casefold() in {v.casefold() for v in logins}:
                raise WorkflowError('The monitoring organiser cannot be a participating annotator.')
            opposite = {m.login.casefold() for m in s.scalars(select(EventMember).where(EventMember.group != group))}
        if opposite.intersection(v.casefold() for v in logins):
            raise WorkflowError('A person can belong to only one event group.')
        role = gateway.role_id('manager' if group == 'monitors' else 'annotator')
        with self.operation(actor, key, 'members', {'group': group, 'logins': logins}) as checkpoint:
            for login in logins:
                with self.sessions() as s:
                    opposite = {m.login.casefold() for m in s.scalars(select(EventMember).where(EventMember.group != group))}
                if login.casefold() in opposite:
                    raise WorkflowError('This person was added to the other event group. Refresh setup.')
                member = gateway.add_login(tid, login, role)
                with self.sessions() as s:
                    if group == 'annotators' and member.id == s.get(EventConfig, 1).owner_id:
                        raise WorkflowError('The monitoring organiser cannot be a participating annotator.')
                checkpoint({'group': group, 'user_id': member.id, 'login': member.login})
                if group == 'monitors' and member.role not in ('admin', 'manager'):
                    gateway.api.user.change_team_role(member.id, tid, role)
                with self.sessions.begin() as s:
                    old = s.get(EventMember, member.id)
                    if old and old.group != group:
                        raise WorkflowError('This account is already in the other event group.')
                    if not old:
                        s.add(EventMember(user_id=member.id, group=group, login=member.login,
                                          name=member.name or member.login))

    def remove_member(self, gateway, actor, key, user_id):
        user_id = positive(user_id, 'User ID')
        with self.sessions() as s:
            member = s.get(EventMember, user_id)
            if not member:
                raise WorkflowError('This person is not in an event group.')
            if any(user_id == t['monitor_id'] or user_id in t['annotator_ids'] for t in self.roster()):
                raise WorkflowError('This person is assigned to a participant pair. Resolve the assignment before removing them.')
            config = s.get(EventConfig, 1)
            tid = config.monitoring_team_id if member.group == 'monitors' else config.annotator_team_id
        with self.operation(actor, key, 'member_remove', {'user_id': user_id, 'group': member.group}) as checkpoint:
            if any(user_id == t['monitor_id'] or user_id in t['annotator_ids'] for t in self.roster()):
                raise WorkflowError('This person was assigned to a pair while you were removing them. Refresh setup.')
            # Never remove the organiser's native administrative membership.
            if user_id != config.owner_id:
                gateway.api.user.remove_from_team(user_id, tid)
            checkpoint({'removed_user_id': user_id, 'team_id': tid})
            with self.sessions.begin() as s:
                s.delete(s.get(EventMember, user_id))

    @contextmanager
    def operation(self, actor, key, kind, details, planned=False):
        if not KEY.fullmatch(str(key)):
            raise WorkflowError('Invalid operation ID. Reload setup and try again.')
        with self.sessions.begin() as s:
            acquired = s.execute(update(SetupState).where(SetupState.id == 1,
                SetupState.operation_id.is_(None)).values(operation_id=key))
            if acquired.rowcount != 1:
                raise WorkflowError('Another setup operation is running or needs inspection. Refresh setup.')
            existing = s.get(SetupOperation, key)
            if planned:
                if not existing or existing.kind != kind or existing.state != 'planned':
                    raise WorkflowError('This plan has already been applied or cannot be applied.')
                existing.state, existing.actor_id = 'applying', actor
            else:
                if existing:
                    raise WorkflowError('This operation was already received. Inspect its status before continuing.')
                s.add(SetupOperation(id=key, actor_id=actor, kind=kind, state='applying', details=details))
        try:
            yield lambda info: self.checkpoint(key, info)
        except Exception as exc:
            # Native API creates can succeed before timing out. Keep the global lock until inspection.
            message = str(exc) if isinstance(exc, WorkflowError) else 'Supervisely or storage operation failed.'
            with self.sessions.begin() as s:
                op = s.get(SetupOperation, key)
                op.state = 'needs_inspection'
                op.result = {**op.result, 'error': message}
            raise WorkflowError(f'{message} Operation {key} needs inspection; resource IDs are in setup history.') from None
        else:
            with self.sessions.begin() as s:
                s.get(SetupOperation, key).state = 'completed'
                s.execute(update(SetupState).where(SetupState.id == 1,
                    SetupState.operation_id == key).values(operation_id=None))

    def checkpoint(self, key, info):
        with self.sessions.begin() as s:
            op = s.get(SetupOperation, key)
            resources = list(op.result.get('resources', []))
            resources.append(info)
            op.result = {**op.result, 'resources': resources}

    def acknowledge(self, actor, key, note):
        if not note or not note.strip() or len(note) > 2000:
            raise WorkflowError('Record what you verified in Supervisely before unblocking setup.')
        with self.sessions.begin() as s:
            op = s.get(SetupOperation, key)
            if not op or op.state != 'needs_inspection':
                raise WorkflowError('Only an operation marked needs inspection can be acknowledged.')
            op.result = {**op.result, 'inspection': note.strip(), 'inspected_by': actor}
            op.state = 'inspected'
            s.execute(update(SetupState).where(SetupState.id == 1,
                SetupState.operation_id == key).values(operation_id=None))

    def create_team(self, gateway, actor, key, name, logins, monitor_id=None, existing_team_id=None):
        name = str(name).strip()
        logins = [str(v).strip() for v in logins]
        if not name or len(name) > 180 or len(logins) != 2 or any(not v or len(v) > 180 for v in logins):
            raise WorkflowError('Enter a team name and the exact logins of two registered annotators.')
        if len({v.casefold() for v in logins}) != 2:
            raise WorkflowError('Choose two distinct annotators.')
        monitor = gateway.monitor(self.monitoring_team_id, positive(monitor_id, 'Monitor ID')) if monitor_id else None
        if monitor and monitor.login.casefold() in {v.casefold() for v in logins}:
            raise WorkflowError('The monitor must be distinct from both annotators.')
        groups = self.groups()
        if groups['ready']:
            available = {u['login'].casefold(): u for u in groups['annotators']}
            if any(v.casefold() not in available for v in logins):
                raise WorkflowError('Choose both participants from the Annotators group.')
            if monitor and monitor.id not in {u['id'] for u in groups['monitors']}:
                raise WorkflowError('Choose a monitor from the Monitors group.')
            paired = {uid for t in self.roster() for uid in t['annotator_ids']}
            if any(available[v.casefold()]['id'] in paired for v in logins):
                raise WorkflowError('Choose unpaired members of the Annotators group.')
        role_id = gateway.role_id('annotator')
        if existing_team_id:
            remote = gateway.api.team.get_info_by_id(positive(existing_team_id, 'Team ID'))
            if not remote or remote.id in (self.monitoring_team_id, groups['annotator_team_id']):
                raise WorkflowError('Choose a participant team separate from the monitoring team.')
            current = gateway.api.user.get_member_info_by_id(remote.id, actor)
            if not current or current.role != 'admin':
                raise WorkflowError('You need Admin permissions in the existing participant team.')
        else:
            remote = None
        roster = self.roster()
        if any((remote and t['id'] == remote.id) or t['name'] == name for t in roster):
            raise WorkflowError('This participant team is already registered.')
        mid = monitor.id if monitor else 0
        with self.operation(actor, key, 'team', {'name': name, 'logins': logins, 'monitor_id': mid}) as checkpoint:
            # Recheck inside the setup lock, so a second organiser cannot register the same team.
            if any((remote and t['id'] == remote.id) or t['name'] == name for t in self.roster()):
                raise WorkflowError('This team was registered by another organiser. Refresh setup.')
            if groups['ready']:
                fresh_groups = self.groups()
                fresh = {u['login'].casefold(): u['id'] for u in fresh_groups['annotators']}
                used = {u for t in self.roster() for u in t['annotator_ids']}
                if any(v.casefold() not in fresh or fresh[v.casefold()] in used for v in logins):
                    raise WorkflowError('The annotator group changed. Refresh setup.')
                if monitor and monitor.id not in {u['id'] for u in fresh_groups['monitors']}:
                    raise WorkflowError('The monitor group changed. Refresh setup.')
            remote = remote or gateway.api.team.create(name, description=f'Nightjar participant pair. Setup {key}')
            checkpoint({'team_id': remote.id})
            members = [gateway.add_login(remote.id, login, role_id) for login in logins]
            ids = [m.id for m in members]
            used = {u for t in self.roster() for u in t['annotator_ids']}
            if len(set(ids)) != 2 or (monitor and monitor.id in ids) or used.intersection(ids):
                raise WorkflowError('Annotators must be distinct, assigned to only one participant pair, and separate from their monitor.')
            if monitor:
                gateway.add_monitor(remote.id, monitor)
            with self.sessions.begin() as s:
                s.add(Team(id=remote.id, name=remote.name, monitor_id=mid, annotator_ids=ids))
                s.add(Audit(team_id=remote.id, batch_id='', actor_id=actor, action='team_registered',
                    details={'operation_id': key, 'monitor_id': mid, 'annotator_ids': ids}))
        return remote.id

    def register_team(self, gateway, actor, key, name, logins, monitor_id=None, existing_team_id=None):
        """The Add team form accepts both participants without a separate roster step."""
        if not KEY.fullmatch(str(key)):
            raise WorkflowError('Invalid operation ID. Reload and try again.')
        logins = [str(v).strip() for v in logins]
        if (not str(name).strip() or len(str(name).strip()) > 180 or len(logins) != 2
                or any(not v or len(v) > 180 for v in logins) or len({v.casefold() for v in logins}) != 2):
            raise WorkflowError('Enter a team name and two distinct registered participant logins.')
        groups = self.groups()
        if not groups['ready']:
            raise WorkflowError('Initialize the event before adding teams.')
        roster = self.roster()
        if any(t['name'] == str(name).strip() or (existing_team_id and t['id'] == int(existing_team_id)) for t in roster):
            raise WorkflowError('This team is already registered.')
        with self.sessions() as s:
            owner_login = s.get(EventConfig, 1).owner_login or ''
        forbidden = {u['login'].casefold() for u in groups['monitors']} | {owner_login.casefold()}
        if forbidden.intersection(v.casefold() for v in logins):
            raise WorkflowError('Participants must be annotators, separate from the monitoring staff.')
        participants = {u['login'].casefold(): u for u in groups['annotators']}
        used = {uid for t in roster for uid in t['annotator_ids']}
        if any(v.casefold() in participants and participants[v.casefold()]['id'] in used for v in logins):
            raise WorkflowError('Each participant can belong to only one team.')
        if monitor_id:
            monitor = gateway.monitor(self.monitoring_team_id, positive(monitor_id, 'Monitor ID'))
            if monitor.id not in {u['id'] for u in groups['monitors']}:
                raise WorkflowError('Choose a registered monitor.')
        missing = [v for v in logins if v.casefold() not in participants]
        if missing:
            self.add_members(gateway, actor, fingerprint([key, 'members'])[:32], 'annotators', missing)
        return self.create_team(gateway, actor, fingerprint([key, 'team'])[:32], name, logins,
                                monitor_id, existing_team_id)

    def unassign_monitor(self, actor, key, team_id, revision, expected_monitor_id):
        team_id = positive(team_id, 'Team ID')
        with self.sessions() as s:
            team = s.get(Team, team_id)
            if not team or not team.monitor_id or team.monitor_id != int(expected_monitor_id):
                raise WorkflowError('This team is no longer assigned to the selected monitor. Reload the list.')
            if team.revision != int(revision):
                raise WorkflowError('Team assignment changed. Reload the list.')
            if s.scalar(select(Batch.id).where(Batch.team_id == team_id, Batch.state != 'locked').limit(1)):
                raise WorkflowError('A released team needs a native reviewer handover before its monitor can be removed.')
        with self.operation(actor, key, 'unassignment', {'team_id': team_id, 'monitor_id': expected_monitor_id}):
            with self.sessions.begin() as s:
                if s.scalar(select(Batch.id).where(Batch.team_id == team_id, Batch.state != 'locked').limit(1)):
                    raise WorkflowError('A job was released while removing the assignment. Inspect the reviewer first.')
                changed = s.execute(update(Team).where(Team.id == team_id, Team.revision == int(revision),
                    Team.monitor_id == int(expected_monitor_id)).values(monitor_id=0, revision=Team.revision + 1))
                if changed.rowcount != 1:
                    raise WorkflowError('Team assignment changed. Reload the list.')
                s.add(Audit(team_id=team_id, batch_id='', actor_id=actor, action='monitor_unassigned',
                    details={'previous_monitor_id': int(expected_monitor_id), 'operation_id': key}))

    def assign_monitor(self, gateway, actor, key, team_id, monitor_id, revision):
        monitor = gateway.monitor(self.monitoring_team_id, positive(monitor_id, 'Monitor ID'))
        if self.groups()['ready'] and monitor.id not in {u['id'] for u in self.groups()['monitors']}:
            raise WorkflowError('Choose a monitor from the Monitors group.')
        with self.sessions() as s:
            team = s.get(Team, positive(team_id, 'Team ID'))
            if not team or monitor.id in team.annotator_ids:
                raise WorkflowError('Select a registered team and a distinct monitor.')
            if team.revision != int(revision):
                raise WorkflowError('Team assignment changed. Refresh setup.')
            if s.scalar(select(Batch.id).where(Batch.team_id == team.id, Batch.state != 'locked').limit(1)):
                raise WorkflowError('Reassign before releasing jobs. Active native reviewers require a separate handover.')
        with self.operation(actor, key, 'assignment', {'team_id': team.id, 'monitor_id': monitor.id}) as checkpoint:
            if self.groups()['ready'] and monitor.id not in {u['id'] for u in self.groups()['monitors']}:
                raise WorkflowError('The monitor group changed. Refresh setup.')
            # Reserve a team revision before remote role changes; normal release uses the same CAS.
            with self.sessions.begin() as s:
                changed = s.execute(update(Team).where(Team.id == team.id, Team.revision == int(revision))
                    .values(revision=Team.revision + 1))
                if changed.rowcount != 1:
                    raise WorkflowError('Team assignment changed. Refresh setup.')
                if s.scalar(select(Batch.id).where(Batch.team_id == team.id, Batch.state != 'locked').limit(1)):
                    raise WorkflowError('A job was released while assigning. Inspect its reviewer first.')
            gateway.add_monitor(team.id, monitor)
            checkpoint({'team_id': team.id, 'monitor_id': monitor.id})
            with self.sessions.begin() as s:
                current = s.get(Team, team.id)
                old = current.monitor_id
                current.monitor_id = monitor.id
                s.add(Audit(team_id=team.id, batch_id='', actor_id=actor, action='monitor_assigned',
                    details={'previous_monitor_id': old, 'monitor_id': monitor.id, 'operation_id': key}))

    def preview_monitor_assignments(self, gateway, actor, monitor_ids):
        monitors = {m['id'] for m in self.groups()['monitors']}
        if not monitor_ids or len(set(monitor_ids)) != len(monitor_ids) or not set(monitor_ids) <= monitors:
            raise WorkflowError('Choose one or more members of the Monitors group.')
        for uid in monitor_ids:
            gateway.monitor(self.monitoring_team_id, uid)
        roster = self.roster()
        unassigned = sorted([t for t in roster if not t['monitor_id']], key=lambda t: (t['name'], t['id']))
        if not unassigned:
            raise WorkflowError('All participant pairs already have monitors.')
        loads = {uid: sum(t['monitor_id'] == uid for t in roster) for uid in monitor_ids}
        rows = []
        for t in unassigned:
            uid = min(monitor_ids, key=lambda u: (loads[u], u))
            rows.append({'team_id': t['id'], 'team_name': t['name'], 'monitor_id': uid, 'revision': t['revision']})
            loads[uid] += 1
        key = uuid4().hex
        details = {'rows': rows, 'roster_hash': fingerprint(roster), 'monitors': sorted(monitors)}
        with self.sessions.begin() as session:
            session.add(SetupOperation(id=key, actor_id=actor, kind='monitor_plan', state='planned', details=details))
        return key, rows

    def apply_monitor_assignments(self, gateway, actor, key):
        with self.sessions() as s:
            op = s.get(SetupOperation, key)
            if not op or op.kind != 'monitor_plan' or op.state != 'planned':
                raise WorkflowError('Choose an unapplied monitor assignment preview.')
            details = op.details
        if (fingerprint(self.roster()) != details['roster_hash']
                or sorted(m['id'] for m in self.groups()['monitors']) != details['monitors']):
            raise WorkflowError('People or pair assignments changed. Preview again.')
        monitors = {row['monitor_id']: gateway.monitor(self.monitoring_team_id, row['monitor_id'])
                    for row in details['rows']}
        with self.operation(actor, key, 'monitor_plan', details, planned=True) as checkpoint:
            if (fingerprint(self.roster()) != details['roster_hash']
                    or sorted(m['id'] for m in self.groups()['monitors']) != details['monitors']):
                raise WorkflowError('People or pair assignments changed. Preview again after inspecting this operation.')
            with self.sessions.begin() as s:
                for row in details['rows']:
                    changed = s.execute(update(Team).where(Team.id == row['team_id'],
                        Team.revision == row['revision'], Team.monitor_id == 0).values(revision=Team.revision + 1))
                    if changed.rowcount != 1:
                        raise WorkflowError('Pair assignment changed. Inspect this operation and refresh.')
            for row in details['rows']:
                gateway.add_monitor(row['team_id'], monitors[row['monitor_id']])
                checkpoint({'team_id': row['team_id'], 'monitor_id': row['monitor_id']})
            with self.sessions.begin() as s:
                for row in details['rows']:
                    s.get(Team, row['team_id']).monitor_id = row['monitor_id']
                    s.add(Audit(team_id=row['team_id'], batch_id='', actor_id=actor,
                        action='monitor_assigned', details={'previous_monitor_id': 0,
                        'monitor_id': row['monitor_id'], 'operation_id': key}))

    def stage(self, actor, key, name, size, offset, encoded):
        if not KEY.fullmatch(str(key)) or not isinstance(name, str) or not name or len(name) > 180:
            raise WorkflowError('Invalid file upload.')
        if '/' in name or '\\' in name or name in ('.', '..'):
            raise WorkflowError('Upload filenames must not contain paths.')
        size = positive(size, 'File size')
        offset = int(offset)
        limit = int(os.getenv('UPLOAD_MAX_FILE_MB', '1024')) * 1024 * 1024
        if size > limit or offset < 0:
            raise WorkflowError(f'File exceeds the configured limit ({limit // 1024 // 1024} MB).')
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise WorkflowError('Invalid upload chunk.') from None
        if not chunk or len(chunk) > 4 * 1024 * 1024 or offset + len(chunk) > size:
            raise WorkflowError('Invalid upload chunk size.')
        with self.sessions.begin() as s:
            upload = s.get(Upload, key)
            if upload is None:
                if offset != 0:
                    raise WorkflowError('Restart this upload from the first chunk.')
                upload = Upload(id=key, actor_id=actor, name=name, size=size, offset=0)
                s.add(upload)
                s.flush()
            if upload.actor_id != actor or upload.name != name or upload.size != size or upload.consumed:
                raise WorkflowError('This upload belongs to another user or was already consumed.')
            changed = s.execute(update(Upload).where(Upload.id == key, Upload.offset == offset,
                Upload.consumed.is_(False)).values(offset=offset + len(chunk)))
            if changed.rowcount != 1:
                raise WorkflowError('Upload offset changed. Restart this file with a new upload ID.')
            folder = self.upload_dir / key
            folder.mkdir(parents=True, exist_ok=True)
            # Each chunk has its own file. Its database offset advances only after durable write.
            with (folder / str(offset)).open('wb') as f:
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
        return offset + len(chunk)

    def upload(self, gateway, actor, key, upload_ids, workspace_id, name, kind, meta):
        if kind not in EXTENSIONS or not upload_ids or len(set(upload_ids)) != len(upload_ids):
            raise WorkflowError('Select one or more image or video files.')
        if not isinstance(name, str) or not name.strip() or len(name) > 180:
            raise WorkflowError('Enter a source project name.')
        # Validate annotation metadata with the pinned SDK, before creating anything remotely.
        import supervisely as sly
        try:
            meta = sly.ProjectMeta.from_json(meta or pilot_meta()).to_json()
        except Exception:
            raise WorkflowError('The annotation metadata is not valid Supervisely meta.json.') from None
        with self.sessions() as s:
            uploads = [s.get(Upload, uid) for uid in upload_ids]
            if any(not u or u.actor_id != actor or u.offset != u.size or u.consumed for u in uploads):
                raise WorkflowError('Every selected file must be fully uploaded by the current organiser.')
            names = [Path(u.name).stem + Path(u.name).suffix.lower() for u in uploads]
            if len(set(names)) != len(uploads):
                raise WorkflowError('Filenames must be unique in the source dataset.')
            if any(Path(u.name).suffix.lower() not in EXTENSIONS[kind] for u in uploads):
                raise WorkflowError('Select supported files of the chosen media type.')
        workspace = gateway.api.workspace.get_info_by_id(positive(workspace_id, 'Workspace ID'))
        if not workspace or workspace.team_id != self.monitoring_team_id:
            raise WorkflowError('Upload destination must belong to the monitoring team.')
        with self.operation(actor, key, 'upload', {'names': [u.name for u in uploads], 'kind': kind}) as checkpoint:
            paths = []
            for upload in uploads:
                folder = self.upload_dir / upload.id
                path = folder / ('media' + Path(upload.name).suffix.lower())
                offset = 0
                with path.open('wb') as out:
                    while offset < upload.size:
                        content = (folder / str(offset)).read_bytes()
                        if not content or offset + len(content) > upload.size:
                            raise WorkflowError('Staged file is incomplete. Restart this upload.')
                        out.write(content)
                        offset += len(content)
                # Keep the API name and temporary path extensions equal, including uppercase inputs.
                paths.append((Path(upload.name).stem + Path(upload.name).suffix.lower(), path))
            with self.sessions.begin() as s:
                for u in uploads:
                    changed = s.execute(update(Upload).where(Upload.id == u.id,
                        Upload.consumed.is_(False)).values(consumed=True))
                    if changed.rowcount != 1:
                        raise WorkflowError('A staged file was already used by another operation.')
            result = gateway.upload(self.monitoring_team_id, workspace.id, name.strip(), kind, paths, meta, checkpoint)
            checkpoint(result)
        # Delete temporary copies only after confirmed remote success.
        import shutil
        for u in uploads:
            shutil.rmtree(self.upload_dir / u.id, ignore_errors=True)
        return result

    def preview(self, gateway, actor, dataset_ids, team_ids, replicas, batch_units, image_units, pilot, with_annotations):
        roster = [t for t in self.roster() if t['id'] in team_ids]
        if not roster or len(roster) != len(set(team_ids)) or any(t['has_batches'] for t in roster):
            raise WorkflowError('Choose registered teams that have not received batches yet.')
        if any(not t['monitor_id'] for t in roster):
            raise WorkflowError('Assign every selected participant pair to a monitor before distributing data.')
        if not dataset_ids or len(set(dataset_ids)) != len(dataset_ids):
            raise WorkflowError('Choose distinct source datasets.')
        image_units = positive(image_units, 'Image effort units')
        replicas, batch_units = positive(replicas, 'Replicas'), positive(batch_units, 'Batch effort units')
        assets, metas = gateway.inventory(self.monitoring_team_id, dataset_ids, image_units)
        if any(not m.get('classes') for m in metas.values()):
            raise WorkflowError('Configure annotation classes on every source project in Supervisely before distribution.')
        try:
            allocation = allocate([t['id'] for t in roster], assets, replicas, batch_units, pilot=pilot)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from None
        if any(not a['image_batches'] and a['video'] is None for a in allocation.values()):
            raise WorkflowError('Some selected teams would have no work. Select fewer teams or increase replication.')
        assignments = []
        for team in roster:
            planned = allocation[team['id']]
            # SDK copy_batch requires one source dataset per call. Partition batches accordingly.
            groups = []
            for batch in planned['image_batches']:
                for dataset_id in sorted({a['dataset_id'] for a in batch}):
                    groups.append([a for a in batch if a['dataset_id'] == dataset_id])
            if planned['video']:
                groups.append([planned['video']])
            assignments.append({'team': team, 'groups': groups, 'units': planned['units']})
        preview = {'pilot': pilot, 'replicas': replicas, 'source_images': sum(a['kind'] == 'images' for a in assets),
            'source_videos': sum(a['kind'] == 'video' for a in assets),
            'teams': [{'id': a['team']['id'], 'name': a['team']['name'], 'monitor_id': a['team']['monitor_id'],
                'images': sum(len(g) for g in a['groups'] if g[0]['kind'] == 'images'),
                'videos': sum(g[0]['kind'] == 'video' for g in a['groups']),
                'batches': len(a['groups']), 'units': a['units']} for a in assignments]}
        key = uuid4().hex
        details = {'dataset_ids': dataset_ids, 'assignments': assignments, 'inventory_hash': fingerprint(assets),
                   'metas': metas, 'image_units': image_units, 'with_annotations': with_annotations, 'preview': preview}
        with self.sessions.begin() as s:
            s.add(SetupOperation(id=key, actor_id=actor, kind='distribution', state='planned', details=details))
        return key, preview

    def distribute(self, gateway, actor, key):
        with self.sessions() as s:
            op = s.get(SetupOperation, key)
            if not op or op.kind != 'distribution' or op.state != 'planned':
                raise WorkflowError('Choose an unapplied distribution preview.')
            details = op.details
        # Repeat the source/roster checks before any remote mutation.
        assets, metas = gateway.inventory(self.monitoring_team_id, details['dataset_ids'], details['image_units'])
        if fingerprint(assets) != details['inventory_hash'] or fingerprint(metas) != fingerprint(details['metas']):
            raise WorkflowError('Source inventory or annotation metadata changed. Create a new preview.')
        for assignment in details['assignments']:
            gateway.monitor(self.monitoring_team_id, assignment['team']['monitor_id'])
        current = {t['id']: t for t in self.roster()}
        if any(current.get(a['team']['id']) != a['team'] for a in details['assignments']):
            raise WorkflowError('Participant roster changed. Create a new preview.')
        with self.operation(actor, key, 'distribution', details, planned=True) as checkpoint:
            current = {t['id']: t for t in self.roster()}
            for assignment in details['assignments']:
                t = assignment['team']
                if current.get(t['id']) != t:
                    raise WorkflowError('Participant roster changed. Inspect this operation and make a new preview.')
            batches = []
            for assignment in details['assignments']:
                t = assignment['team']
                monitor = gateway.monitor(self.monitoring_team_id, t['monitor_id'])
                gateway.add_monitor(t['id'], monitor)
                for index, group in enumerate(assignment['groups'], 1):
                    callback = lambda info, tid=t['id']: checkpoint({'team_id': tid, **info})
                    dataset_id, mapped = gateway.copy_group(t['id'], key, index, group,
                        details['metas'][str(group[0]['dataset_id'])], details['with_annotations'], callback)
                    batches.append(Batch(id=f'{key}_t{t["id"]}_b{index}', team_id=t['id'], position=index,
                        kind=group[0]['kind'], dataset_id=dataset_id, assets=mapped))
            # Publish all mappings atomically only after all remote copies are confirmed.
            with self.sessions.begin() as s:
                s.add_all(batches)
                for a in details['assignments']:
                    s.execute(update(Team).where(Team.id == a['team']['id']).values(revision=Team.revision + 1))
                    s.add(Audit(team_id=a['team']['id'], batch_id='', actor_id=actor, action='data_distributed',
                        details={'operation_id': key, 'pilot': details['preview']['pilot']}))
            checkpoint({'batches_created': len(batches)})
