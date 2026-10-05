"""Provisioning uses the organiser's current credential, never a browser-stored token."""
from .gateway import Gateway
from .service import WorkflowError


class SetupGateway(Gateway):
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
                            for d in self.api.dataset.get_list(p.id):
                                datasets.append({'id': d.id, 'name': f'{w.name} / {p.name} / {d.name}',
                                                 'kind': 'images' if p.type == 'images' else 'video'})
        monitors = ([self.user_view(u) for u in self.api.user.get_team_members(monitoring_team_id)
                    if not u.disabled and u.role in ('admin', 'manager')]
                    if monitoring_team_id and monitoring_team_id in {t.id for t in teams} else [])
        return {'users': sorted(users.values(), key=lambda u: u['login']), 'monitors': monitors,
                'workspaces': [w for w in workspaces if w['team_id'] == monitoring_team_id],
                'datasets': datasets, 'remote_teams': [{'id': t.id, 'name': t.name} for t in teams]}

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
            raise WorkflowError(f'Could not verify active membership for {login}.')
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
                    'frames': frames, 'units': frames if kind == 'video' else image_units})
        return assets, metas

    def upload(self, monitoring_team_id, workspace_id, name, kind, paths, meta, checkpoint):
        workspace = self.api.workspace.get_info_by_id(workspace_id)
        if not workspace or workspace.team_id != monitoring_team_id:
            raise WorkflowError('Upload destination must be a workspace in the monitoring team.')
        p = self.api.project.create(workspace_id, name, type='images' if kind == 'images' else 'videos')
        checkpoint({'project_id': p.id})
        self.api.project.update_meta(p.id, meta)
        d = self.api.dataset.create(p.id, 'source')
        checkpoint({'dataset_id': d.id})
        api = self.api.image if kind == 'images' else self.api.video
        for filename, path in paths:
            entities = api.upload_paths(d.id, [filename], [str(path)])
            if len(entities) != 1:
                raise WorkflowError('Unexpected upload response. Inspect the source project before retrying.')
            checkpoint({'uploaded': [entities[0].id]})
        return {'project_id': p.id, 'dataset_id': d.id, 'name': name}

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
            mapped = {'source_id': a['source_id'], 'entity_id': e.id}
            if kind == 'video':
                if e.frames_count != a['frames']:
                    raise WorkflowError('Copied video frame count differs from the original.')
                mapped['frames'] = a['frames']
            result.append(mapped)
        checkpoint({'entities': [e.id for e in copied]})
        return d.id, result
