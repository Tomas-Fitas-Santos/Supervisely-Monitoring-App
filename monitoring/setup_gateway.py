"""Provisioning uses the organiser's current credential, never a browser-stored token."""
from .gateway import Gateway
from .service import WorkflowError


class InvitationRejected(WorkflowError):
    """A definitive native validation/permission rejection, safe to retry explicitly."""


class SetupGateway(Gateway):
    def invitation_members(self, team_id):
        """Keep raw invitation fields; UserInfo drops activated/inviteEmail.

        members.list's documented showInvited=False selects accepted members,
        including on instances which omit the activated field in their response.
        """
        def listing(invited):
            result, page = [], 1
            while True:
                payload = self.api.post('members.list', {'teamId': int(team_id), 'page': page,
                    'showInvited': invited, 'showUnregistered': invited}).json()
                if not isinstance(payload, dict) or not isinstance(payload.get('entities'), list):
                    raise WorkflowError('Supervisely returned an unsupported membership response. No invitation was marked accepted.')
                result.extend(payload['entities'])
                if page >= payload.get('pagesCount', 1):
                    return result
                page += 1
        all_members, accepted = listing(True), listing(False)
        active_ids = {m.get('id') or m.get('userId') for m in accepted
                      if m.get('activated') is not False and not m.get('disabled') and not m.get('disabledAt')}
        return [dict(m, membership_active=(m.get('id') or m.get('userId')) in active_ids
                     and m.get('activated') is not False and not m.get('disabled') and not m.get('disabledAt'))
                for m in all_members]

    def invite_email(self, team_id, email, role):
        """Same members.add route used by the SDK, with an email identifier.

        Community invitation acceptance is asynchronous. Do not look up the
        email through users.list and do not infer membership from this response.
        """
        try:
            result = self.api.post('members.add', {'teamId': int(team_id), 'login': email,
                                                  'roleId': self.role_id(role)}, retries=1).json()
        except Exception as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in (400, 401, 402, 403, 404, 409, 422):
                raise InvitationRejected(f'Supervisely rejected the invitation (HTTP {status}). Check email, native Admin permissions, plan/seat limits and email-invitation support on this instance.') from None
            raise  # Unknown remote outcome: retain the setup operation checkpoint.
        return {'user_id': result.get('userId') or result.get('id')} if isinstance(result, dict) else {}

    def visible_accounts(self):
        """Use accessible membership data; global listing is an optional capability."""
        people = {}
        for team in self.api.team.get_list():
            for user in self.api.user.get_team_members(team.id):
                people[user.id] = user
        try:
            for user in self.api.user.get_list():
                people[user.id] = user
        except Exception:
            # A normal team Admin may not have instance-wide users.list permission.
            pass
        return list(people.values())

    def lookup_login(self, login):
        try:
            return self.api.user.get_info_by_login(login)
        except Exception:
            return next((u for u in self.visible_accounts() if u.login == login), None)

    def organiser(self, monitoring_team_id, user_id):
        member = self.api.user.get_member_info_by_id(monitoring_team_id, user_id)
        if not member or getattr(member, 'disabled', False) or member.role != 'admin':
            raise WorkflowError('Event setup requires the Admin role in the monitoring team.')

    @staticmethod
    def user_view(user):
        return {'id': user.id, 'login': user.login, 'name': user.name or user.login}

    def catalog(self, monitoring_team_id):
        teams = self.api.team.get_list()
        users, workspaces, datasets = {}, [], []
        for team in teams:
            for u in self.api.user.get_team_members(team.id):
                if not u.disabled:
                    users[u.login] = self.user_view(u)
            for w in self.api.workspace.get_list(team.id):
                workspaces.append({'id': w.id, 'name': f'{team.name} / {w.name}', 'team_id': team.id})
                # Source data is confined to the monitoring team.
                if team.id == monitoring_team_id:
                    for p in self.api.project.get_list(w.id):
                        if p.type in ('images', 'videos'):
                            for d in self.api.dataset.get_list(p.id, recursive=True):
                                datasets.append({'id': d.id, 'name': f'{w.name} / {p.name} / {d.name}',
                                                 'kind': 'images' if p.type == 'images' else 'video'})
        monitors = ([self.user_view(u) for u in self.api.user.get_team_members(monitoring_team_id)
                    if not u.disabled and u.role in ('admin', 'manager')]
                    if monitoring_team_id and monitoring_team_id in {t.id for t in teams} else [])
        return {'users': sorted(users.values(), key=lambda u: u['login']), 'monitors': monitors,
                'workspaces': [w for w in workspaces if w['team_id'] == monitoring_team_id],
                'datasets': datasets, 'remote_teams': [{'id': t.id, 'name': t.name} for t in teams]}

    def sources(self, monitoring_team_id):
        workspaces, datasets = [], []
        if not monitoring_team_id:
            return {'workspaces': [], 'datasets': []}
        for w in self.api.workspace.get_list(monitoring_team_id):
            workspaces.append({'id': w.id, 'name': w.name})
            for p in self.api.project.get_list(w.id):
                if p.type not in ('images', 'videos'):
                    continue
                for d in self.api.dataset.get_list(p.id, recursive=True):
                    datasets.append({'id': d.id, 'name': d.name, 'project': p.name, 'workspace': w.name,
                        'kind': 'images' if p.type == 'images' else 'video', 'items': d.items_count})
        return {'workspaces': workspaces, 'datasets': datasets}

    def monitor(self, monitoring_team_id, user_id):
        member = self.api.user.get_member_info_by_id(monitoring_team_id, user_id)
        if not member or member.disabled or member.role not in ('admin', 'manager'):
            raise WorkflowError('Select an active Admin or Manager from the monitoring team.')
        return member

    def role_id(self, role):
        matches = [r.id for r in self.api.role.get_list() if r.role == role]
        if len(matches) != 1:
            raise WorkflowError(f'This Supervisely instance does not expose the required {role} role.')
        return matches[0]

    def add_login(self, team_id, login, role_id):
        member = self.api.user.get_member_info_by_login(team_id, login)
        if member:
            if member.disabled:
                raise WorkflowError(f'Account {login} is disabled.')
            return member
        # This API accepts an existing login without requiring global users.list privileges.
        self.api.user.add_to_team_by_login(login, team_id, role_id)
        member = self.api.user.get_member_info_by_login(team_id, login)
        if not member or member.disabled:
            raise WorkflowError(f'Invitation pending or membership unavailable for {login}. Accept the native team invitation, then inspect and resume setup.')
        return member

    def add_monitor(self, team_id, monitor):
        current = self.api.user.get_member_info_by_id(team_id, monitor.id)
        manager = self.role_id('manager')
        if current:
            if current.disabled:
                raise WorkflowError('The selected monitor is disabled in the participant team.')
            if current.role not in ('admin', 'manager'):
                self.api.user.change_team_role(monitor.id, team_id, manager)
        else:
            self.add_login(team_id, monitor.login, manager)

    def source_dataset(self, monitoring_team_id, dataset_id, expected=None):
        d = self.api.dataset.get_info_by_id(dataset_id)
        if d is None:
            raise WorkflowError('Source dataset is unavailable.')
        p = self.api.project.get_info_by_id(d.project_id)
        w = self.api.workspace.get_info_by_id(p.workspace_id)
        kind = 'images' if p.type == 'images' else 'video' if p.type == 'videos' else None
        if w.team_id != monitoring_team_id or kind is None or (expected and kind != expected):
            raise WorkflowError('Select an image/video source dataset inside the monitoring team.')
        return d, p, kind

    def inventory(self, monitoring_team_id, dataset_ids, image_units):
        assets, metas = [], {}
        for dataset_id in dataset_ids:
            d, p, kind = self.source_dataset(monitoring_team_id, dataset_id)
            metas[str(dataset_id)] = self.api.project.get_meta(p.id)
            entities = (self.api.image if kind == 'images' else self.api.video).get_list(d.id)
            for entity in entities:
                frames = entity.frames_count if kind == 'video' else None
                if kind == 'video' and (not isinstance(frames, int) or frames <= 0):
                    raise WorkflowError('A video is not ready or has no valid frame count.')
                assets.append({'source_id': f'{kind}:{entity.id}', 'entity_id': entity.id,
                    'dataset_id': d.id, 'project_id': p.id, 'name': entity.name, 'kind': kind,
                    'frames': frames, 'units': frames if kind == 'video' else image_units,
                    'stratum': str(getattr(d, 'parent_id', None) or d.id),
                    'relative_path': f'{p.name}/{d.name}/{entity.name}'})
        if self.sessions:
            from .db import SourceOrigin
            with self.sessions() as session:
                for asset in assets:
                    origin = session.get(SourceOrigin, asset['source_id'])
                    if origin:
                        asset.update(relative_path=origin.relative_path, checksum=origin.checksum)
        return assets, metas

    def upload(self, monitoring_team_id, workspace_id, name, kind, paths, meta, checkpoint):
        from pathlib import PurePosixPath
        import hashlib
        workspace = self.api.workspace.get_info_by_id(workspace_id)
        if not workspace or workspace.team_id != monitoring_team_id:
            raise WorkflowError('Upload destination must be a workspace in the monitoring team.')
        from .setup import EXTENSIONS
        projects, datasets, uploaded = {}, {}, []
        for filename, path in paths:
            parts = PurePosixPath(filename).parts
            media_kind = next((k for k, exts in EXTENSIONS.items() if k != 'mixed' and path.suffix.lower() in exts), None)
            if media_kind is None or (kind != 'mixed' and media_kind != kind):
                raise WorkflowError('Unsupported media type in dataset folder.')
            if media_kind not in projects:
                project = self.api.project.create(workspace_id, name + (' images' if media_kind == 'images' else ' videos'),
                                                  type='images' if media_kind == 'images' else 'videos')
                projects[media_kind] = project
                checkpoint({'project_id': project.id})
                self.api.project.update_meta(project.id, meta)
            project = projects[media_kind]
            parent = None
            folders = parts[:-1] or ('source',)
            for i, folder in enumerate(folders):
                key = (media_kind, folders[:i+1])
                if key not in datasets:
                    ds = self.api.dataset.create(project.id, folder, **({'parent_id': parent} if parent else {}))
                    datasets[key] = ds
                    checkpoint({'dataset_id': ds.id})
                parent = datasets[key].id
            api = self.api.image if media_kind == 'images' else self.api.video
            entities = api.upload_paths(parent, [parts[-1]], [str(path)])
            if len(entities) != 1:
                raise WorkflowError('Unexpected upload response. Inspect resources before retrying.')
            checkpoint({'uploaded': [entities[0].id]})
            with path.open('rb') as source_file:
                checksum = hashlib.file_digest(source_file, 'sha256').hexdigest()
            uploaded.append({'source_id': f'{media_kind}:{entities[0].id}', 'relative_path': filename,
                             'checksum': checksum})
        return {'project_id': next(iter(projects.values())).id, 'dataset_id': next(reversed(datasets.values())).id,
                'project_ids': [p.id for p in projects.values()], 'dataset_ids': [d.id for d in datasets.values()], 'sources': uploaded}

    def copy_group(self, team_id, operation_id, index, group, meta, with_annotations, checkpoint):
        # One destination dataset per batch keeps source filenames and metadata unambiguous.
        w = self.api.workspace.get_info_by_name(team_id, f'Nightjar {operation_id[:12]}')
        if w is None:
            w = self.api.workspace.create(team_id, f'Nightjar {operation_id[:12]}')
            checkpoint({'workspace_id': w.id})
        kind = group[0]['kind']
        p = self.api.project.create(w.id, f'Batch {index}', type='images' if kind == 'images' else 'videos')
        checkpoint({'project_id': p.id})
        self.api.project.update_meta(p.id, meta)
        d = self.api.dataset.create(p.id, f'Batch {index}')
        checkpoint({'dataset_id': d.id})
        api = self.api.image if kind == 'images' else self.api.video
        copied = api.copy_batch(d.id, [a['entity_id'] for a in group], with_annotations=with_annotations)
        if (not copied or len(copied) != len(group) or len({e.name for e in copied}) != len(group)
                or len({e.id for e in copied}) != len(group)):
            raise WorkflowError('Copy result differs from the allocation. Inspect operation resource IDs.')
        by_name = {e.name: e for e in copied}
        result = []
        for a in group:
            e = by_name.get(a['name'])
            if not e or e.id == a['entity_id'] or e.dataset_id != d.id:
                raise WorkflowError('Copied entities could not be mapped to their original sources.')
            mapped = {'source_id': a['source_id'], 'entity_id': e.id, 'relative_path': a.get('relative_path', a['name']), 'effort': a['units']}
            if kind == 'video':
                if e.frames_count != a['frames']:
                    raise WorkflowError('Copied video frame count differs from the original.')
                mapped['frames'] = a['frames']
            result.append(mapped)
        checkpoint({'entities': [e.id for e in copied]})
        return d.id, result
