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

from .db import Audit, Batch, SetupOperation, SetupState, Team, Upload
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
        self.monitoring_team_id = monitoring_team_id
        self.upload_dir = Path(upload_dir or os.getenv('UPLOAD_STAGING_DIR', 'data/uploads')).resolve()

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
            return {'roster': self.roster(), 'operations': [
                {'id': o.id, 'kind': o.kind, 'state': o.state, 'actor_id': o.actor_id,
                 'result': o.result, 'created_at': o.created_at,
                 'preview': o.details.get('preview') if o.kind == 'distribution' else None} for o in ops],
                'blocked_by': lock.operation_id if lock else None}

    def authorize(self, gateway, actor):
        if gateway is None:
            raise WorkflowError('Configure API_TOKEN and your real user ID to set up a live pilot.')
        gateway.organiser(self.monitoring_team_id, actor)
        with self.sessions() as s:
            demo = s.scalar(select(Batch).where(Batch.id.like('demo-%')).limit(1))
            if demo and any(a['source_id'].startswith('demo-') for a in demo.assets):
                raise WorkflowError('This database contains synthetic demo data. Use a fresh pilot.db in DATABASE_URL and restart the app.')

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

    def create_team(self, gateway, actor, key, name, logins, monitor_id, existing_team_id=None):
        name = str(name).strip()
        logins = [str(v).strip() for v in logins]
        if not name or len(name) > 180 or len(logins) != 2 or any(not v or len(v) > 180 for v in logins):
            raise WorkflowError('Enter a team name and the exact logins of two registered annotators.')
        if len({v.casefold() for v in logins}) != 2:
            raise WorkflowError('Choose two distinct annotators.')
        monitor = gateway.monitor(self.monitoring_team_id, positive(monitor_id, 'Monitor ID'))
        if monitor.login.casefold() in {v.casefold() for v in logins}:
            raise WorkflowError('The monitor must be distinct from both annotators.')
        role_id = gateway.role_id('annotator')
        if existing_team_id:
            remote = gateway.api.team.get_info_by_id(positive(existing_team_id, 'Team ID'))
            if not remote or remote.id == self.monitoring_team_id:
                raise WorkflowError('Choose a participant team separate from the monitoring team.')
            current = gateway.api.user.get_member_info_by_id(remote.id, actor)
            if not current or current.role != 'admin':
                raise WorkflowError('You need Admin permissions in the existing participant team.')
        else:
            remote = None
        roster = self.roster()
        if any((remote and t['id'] == remote.id) or t['name'] == name for t in roster):
            raise WorkflowError('This participant team is already registered.')
        with self.operation(actor, key, 'team', {'name': name, 'logins': logins, 'monitor_id': monitor.id}) as checkpoint:
            # Recheck inside the setup lock, so a second organiser cannot register the same team.
            if any((remote and t['id'] == remote.id) or t['name'] == name for t in self.roster()):
                raise WorkflowError('This team was registered by another organiser. Refresh setup.')
            remote = remote or gateway.api.team.create(name, description=f'Nightjar participant pair. Setup {key}')
            checkpoint({'team_id': remote.id})
            members = [gateway.add_login(remote.id, login, role_id) for login in logins]
            ids = [m.id for m in members]
            used = {u for t in self.roster() for u in t['annotator_ids']}
            if len(set([*ids, monitor.id])) != 3 or used.intersection(ids):
                raise WorkflowError('Annotators must be distinct, assigned to only one participant pair, and separate from their monitor.')
            gateway.add_monitor(remote.id, monitor)
            with self.sessions.begin() as s:
                s.add(Team(id=remote.id, name=remote.name, monitor_id=monitor.id, annotator_ids=ids))
                s.add(Audit(team_id=remote.id, batch_id='', actor_id=actor, action='team_registered',
                    details={'operation_id': key, 'monitor_id': monitor.id, 'annotator_ids': ids}))
        return remote.id

    def assign_monitor(self, gateway, actor, key, team_id, monitor_id, revision):
        monitor = gateway.monitor(self.monitoring_team_id, positive(monitor_id, 'Monitor ID'))
        with self.sessions() as s:
            team = s.get(Team, positive(team_id, 'Team ID'))
            if not team or monitor.id in team.annotator_ids:
                raise WorkflowError('Select a registered team and a distinct monitor.')
            if team.revision != int(revision):
                raise WorkflowError('Team assignment changed. Refresh setup.')
            if s.scalar(select(Batch.id).where(Batch.team_id == team.id, Batch.state != 'locked').limit(1)):
                raise WorkflowError('Reassign before releasing jobs. Active native reviewers require a separate handover.')
        with self.operation(actor, key, 'assignment', {'team_id': team.id, 'monitor_id': monitor.id}) as checkpoint:
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
