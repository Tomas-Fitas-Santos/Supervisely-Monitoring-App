"""Persistent loopback-only Supervisely simulator for organiser demonstrations.

No network, passwords or actual accounts. The CC0 photos are genuine; users,
annotations and jobs in this adapter are explicitly illustrative.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json
import threading

from sqlalchemy import select, update
from .db import EventConfig, EventMember, Simulation, CompetitionConfig
from .service import WorkflowError
from .pilot import ROOT, accounts
_LOCK = threading.RLock()


class Store:
    def __init__(self, sessions): self.sessions = sessions
    def read(self):
        with self.sessions() as s: return deepcopy(s.get(Simulation, 1).data)
    def mutate(self, callback):
        with _LOCK, self.sessions.begin() as s:
            row = s.get(Simulation, 1)
            data = deepcopy(row.data)
            result = callback(data)
            changed = s.execute(update(Simulation).where(Simulation.id == 1, Simulation.revision == row.revision)
                                .values(data=data, revision=row.revision+1))
            if changed.rowcount != 1: raise WorkflowError('Another demo operation changed the simulator. Refresh.')
            return result
    def add(self, kind, info):
        def add(data):
            data['next_id'] += 1
            item = {**info, 'id': data['next_id']}
            data[kind][str(item['id'])] = item
            return NS(**deepcopy(item))
        return self.mutate(add)
    def edit(self, kind, key, changes):
        return self.mutate(lambda data: data[kind][str(key)].update(changes))


class Resource:
    def __init__(self, api, kind, parent=None): self.api, self.kind, self.parent = api, kind, parent
    def get_info_by_id(self, id):
        item = self.api.store.read()[self.kind].get(str(id))
        if not item: return None
        if self.kind == 'datasets':
            item['items_count'] = sum(x['dataset_id'] == id for x in self.api.store.read()['media'].values())
        return NS(**item)
    def get_list(self, parent_id=None, recursive=False):
        rows = self.api.store.read()[self.kind].values()
        return [self.get_info_by_id(r['id']) for r in rows if (self.parent is None or r[self.parent] == parent_id)
                and (self.kind != 'datasets' or recursive or not r.get('parent_id'))]
    def get_info_by_name(self, parent_id, name):
        return next((r for r in self.get_list(parent_id, recursive=True) if r.name == name), None)
    def create(self, scope_id=None, name=None, **kwargs):
        if self.kind == 'teams':
            name = scope_id if name is None else name
            info = dict(name=name, members={'900':'admin'})
        else:
            info = dict(name=name, **{self.parent: scope_id}, **kwargs)
            if self.kind == 'projects': info.setdefault('meta', {})
            if self.kind == 'datasets': info.setdefault('parent_id', None)
        return self.api.store.add(self.kind, info)
    def get_meta(self, id): return self.get_info_by_id(id).meta
    def update_meta(self, id, meta): self.api.store.edit(self.kind, id, {'meta': meta})
    def get_activity(self, *args, **kwargs): return []


class Users:
    def __init__(self, api): self.api = api
    def get_my_info(self): return self.get_info_by_id(self.api.uid)
    def get_info_by_id(self, id):
        item = self.api.store.read()['users'].get(str(id)); return NS(**item) if item else None
    def get_info_by_login(self, login): return next((u for u in self.get_list() if u.login == login), None)
    def get_list(self, **kwargs): return [NS(**u) for u in self.api.store.read()['users'].values()]
    def get_team_members(self, team_id):
        team = self.api.store.read()['teams'].get(str(team_id))
        if not team: return []
        return [NS(**{**self.api.store.read()['users'][uid], 'role': role, 'role_id': {'admin':1,'manager':3,'annotator':5}[role]})
                for uid, role in team['members'].items()]
    def get_member_info_by_id(self, team_id, id): return next((u for u in self.get_team_members(team_id) if u.id == id), None)
    def get_member_info_by_login(self, team_id, login): return next((u for u in self.get_team_members(team_id) if u.login == login), None)
    def add_to_team_by_login(self, login, team_id, role_id):
        user = self.get_info_by_login(login)
        if not user: raise WorkflowError('The demo has no such account. Use the bundled CSV.')
        def add(data): data['teams'][str(team_id)]['members'][str(user.id)] = {1:'admin',3:'manager',5:'annotator'}[role_id]
        self.api.store.mutate(add)
    def change_team_role(self, uid, team_id, role_id): self.add_to_team_by_login(self.get_info_by_id(uid).login, team_id, role_id)
    def remove_from_team(self, uid, team_id): self.api.store.mutate(lambda d: d['teams'][str(team_id)]['members'].pop(str(uid), None))


class Media(Resource):
    def __init__(self, api, kind):
        super().__init__(api, 'media', 'dataset_id'); self.media_kind = kind
        self.annotation = NS(download=lambda id: self.get_info_by_id(id).annotation)
    def get_list(self, parent_id=None, **kwargs): return [m for m in super().get_list(parent_id, **kwargs) if m.kind == self.media_kind]
    def upload_paths(self, dataset_id, names, paths):
        from PIL import Image
        ds = self.api.dataset.get_info_by_id(dataset_id)
        result = []
        for name, path in zip(names, paths):
            path = Path(path)
            # The adapter copies imported media so clearing upload staging cannot break a demo.
            import shutil
            home = self.api.home / 'media'; home.mkdir(parents=True, exist_ok=True)
            import hashlib
            dst = home / (hashlib.sha256(path.read_bytes()).hexdigest()+path.suffix.lower())
            if path.resolve() != dst.resolve(): shutil.copyfile(path, dst)
            with Image.open(dst) as image: width, height = image.size
            result.append(self.api.store.add('media', dict(name=name, dataset_id=dataset_id, project_id=ds.project_id,
                kind=self.media_kind, path=str(dst), width=width, height=height, annotation={'size':{'height':height,'width':width},'objects':[],'tags':[]})))
        return result
    def copy_batch(self, dataset_id, ids, with_annotations=False):
        result = []
        for id in ids:
            media = vars(self.get_info_by_id(id)); media.pop('id')
            media.update(dataset_id=dataset_id, project_id=self.api.dataset.get_info_by_id(dataset_id).project_id)
            if not with_annotations: media['annotation'] = {'size':{'height':media['height'],'width':media['width']},'objects':[],'tags':[]}
            result.append(self.api.store.add('media', media))
        return result
    def download_np(self, id):
        import numpy as np
        from PIL import Image
        return np.array(Image.open(self.get_info_by_id(id).path).convert('RGB'))


class Jobs(Resource):
    def __init__(self, api): super().__init__(api, 'jobs')
    def create(self, name, dataset_id, user_ids, images_ids=None, reviewer_id=None, description='', **kwargs):
        ds = self.api.dataset.get_info_by_id(dataset_id); project = self.api.project.get_info_by_id(ds.project_id)
        workspace = self.api.workspace.get_info_by_id(project.workspace_id)
        return [self.api.store.add('jobs', dict(name=name, dataset_id=dataset_id, project_id=project.id,
                workspace_id=workspace.id, team_id=workspace.team_id, assigned_to_id=uid, reviewer_id=reviewer_id,
                entities=[{'id':id,'reviewStatus':'none'} for id in images_ids], images_count=len(images_ids),
                status='pending', disabled=False, description=description)) for uid in user_ids]
    def get_list(self, team_id=None, dataset_id=None, **kwargs):
        return [j for j in super().get_list() if (team_id is None or j.team_id == team_id) and (dataset_id is None or j.dataset_id == dataset_id)]
    def get_stats(self, id):
        j = self.get_info_by_id(id)
        return {'job':{'imagesCount':j.images_count,'finishedImagesCount':j.images_count if getattr(j, 'confirmed', False) or j.status in ('on_review','completed','review_completed') else 0},
                'images':{'images':j.entities}}
    def set_assignees(self, id, reviewer_id=None, annotator_id=None, **kwargs):
        changes = {}
        if reviewer_id is not None: changes['reviewer_id'] = reviewer_id
        if annotator_id is not None: changes['assigned_to_id'] = annotator_id
        self.api.store.edit('jobs', id, changes); return self.get_info_by_id(id)
    def set_status(self, id, status):
        changes = {'status':str(status)}
        if status in ('on_review','completed','review_completed'): changes['confirmed']=True
        self.api.store.edit('jobs', id, changes)
    def stop(self, id, *args, **kwargs): self.set_status(id, 'stopped')
    def set_entity_review_status(self, id, entity_id, status):
        j = self.get_info_by_id(id); entities = j.entities
        for e in entities:
            if e['id'] == entity_id: e['reviewStatus'] = status
        self.api.store.edit('jobs', id, {'entities':entities})
    def restart(self, id, title=None, **kwargs):
        j = self.get_info_by_id(id)
        entities = [e['id'] for e in j.entities if e['reviewStatus']=='rejected']
        jobs = self.create(title or j.name, j.dataset_id, [j.assigned_to_id], images_ids=entities, reviewer_id=kwargs.get('reviewer_id',j.reviewer_id),description=j.description)
        self.set_status(id, 'completed'); return [{'id':r.id} for r in jobs]


class DemoApi:
    server_address = 'https://demo.invalid'
    def __init__(self, sessions, uid, home):
        self.store, self.uid, self.home = Store(sessions), uid, Path(home)
        self.user = Users(self)
        self.team = Resource(self, 'teams')
        self.workspace = Resource(self, 'workspaces', 'team_id')
        self.project = Resource(self, 'projects', 'workspace_id')
        self.dataset = Resource(self, 'datasets', 'project_id')
        self.image = Media(self, 'images'); self.video = Media(self, 'video')
        self.annotation = NS(download_json=lambda id: self.image.get_info_by_id(id).annotation)
        self.labeling_job = Jobs(self)
        self.role = NS(get_list=lambda:[NS(id=id,role=role) for id,role in [(1,'admin'),(3,'manager'),(5,'annotator')]])


def initialize_demo(sessions):
    with sessions.begin() as s:
        if s.get(Simulation, 1): return
        from .registration import Registrations
        _, rows = Registrations.columns((ROOT/'registrations.csv').read_text())
        users = {'900':dict(id=900,login='demo_organiser',name='Tomás Santos (simulated admin)',email=accounts()['organiser_email'],disabled=False),
                 '901':dict(id=901,login='demo_monitor',name='Demo monitor',email='monitor@example.test',disabled=False)}
        for i,row in enumerate(rows):
            for n in (1,2):
                uid=1000+i*2+n
                users[str(uid)]=dict(id=uid,login=row[f'member_{n}_login'] or f'demo_member_{n}_{i}',name=row[f'member_{n}_name'],email=row[f'member_{n}_email'],disabled=False)
        s.add(Simulation(id=1,data={'next_id':10000,'users':users,
            'teams':{'10':dict(id=10,name='Demo Monitors',members={'900':'admin','901':'manager'}),'20':dict(id=20,name='Demo Annotators',members={'900':'admin'})},
            'workspaces':{'30':dict(id=30,name='Demo event sources',team_id=10)},'projects':{},'datasets':{},'media':{},'jobs':{}}))
        c=s.get(EventConfig,1); c.owner_id=900;c.owner_login='demo_organiser';c.server_address=DemoApi.server_address
        c.monitoring_team_id=10;c.annotator_team_id=20;c.source_workspace_id=30
        s.add_all([EventMember(user_id=uid,group='monitors',login=users[str(uid)]['login'],name=users[str(uid)]['name']) for uid in (900,901)])
        s.get(CompetitionConfig,1).rules={'name':'Organiser demonstration','replicas':1,'batch_effort':1}


def submit_demo(api, batch):
    """Illustrative labels to exercise reviews, not scientific reference annotations."""
    for asset in batch.assets:
        media=api.image.get_info_by_id(asset['entity_id']); w,h=media.width,media.height
        points={'crown':(w*.51,h*.24),'left_eye':(w*.51,h*.30),'beak':(w*.43,h*.30)}
        objects=[dict(classTitle='bird',geometryType='rectangle',tags=[{'name':'bird_id','value':'bird-1'}],
                      points={'exterior':[[round(w*.42),round(h*.22)],[round(w*.98),round(h*.76)]],'interior':[]})]
        objects += [dict(classTitle=name,geometryType='point',tags=[{'name':'bird_id','value':'bird-1'},{'name':'visibility','value':'visible'}],
                         points={'exterior':[[round(x),round(y)]],'interior':[]}) for name,(x,y) in points.items()]
        api.store.edit('media', media.id, {'annotation':{'size':{'height':h,'width':w},'objects':objects,'tags':[]}})
    for jid in batch.job_ids: api.labeling_job.set_status(jid, 'on_review')


def prepare_presentation(sessions, home):
    """Exercise the application workflow once, only inside a fresh simulator."""
    from . import controller
    from .competition import Competition
    from .db import Batch, Registration, Team
    from .pilot import import_roster, upload_photos
    from .registration import Registrations
    from .service import Service
    from .setup import Setup
    from .setup_gateway import SetupGateway
    with sessions() as session:
        config = session.get(EventConfig, 1)
        if not session.get(Simulation, 1) or config.server_address != DemoApi.server_address:
            raise WorkflowError('Presentation preparation is available only in the isolated simulator.')
        if session.scalar(select(Team.id).limit(1)) or session.scalar(select(Registration.id).limit(1)):
            raise WorkflowError('Continue the existing demo or reset it with a backup before preparing a new presentation.')
    api = DemoApi(sessions, 900, home)
    gateway = SetupGateway(api, sessions)
    setup = Setup(sessions, 10, Path(home) / 'uploads')
    service = Service(sessions)
    import_roster(sessions, 900)
    registrations = Registrations(sessions)
    registrations.resolve(gateway, 900)
    for row in registrations.rows():
        registrations.provision(gateway, setup, 900, row['id'], 900)
    from uuid import uuid4
    result = upload_photos(setup, gateway, 900, uuid4().hex)
    competition = Competition(sessions)
    config = competition.config()
    if not config['frozen']:
        competition.save_rules(900, config['revision'], config['rules'], True)
    team_ids = [team['id'] for team in setup.roster()]
    plan, _ = setup.preview(gateway, 900, result['dataset_ids'], team_ids, 1, 1, 1, True, False)
    setup.distribute(gateway, 900, plan)
    with sessions() as session:
        team = session.get(Team, team_ids[0])
        batch = session.scalar(select(Batch).where(Batch.team_id == team.id).order_by(Batch.position))
    controller.release(service, gateway, 900, team.id, batch.id, team.revision)
    team, batch = service.spec(900, team.id, batch.id)
    submit_demo(api, batch)
    controller.sync(service, gateway, team, batch)
    competition.refresh(gateway, team, batch)


def reset_demo(sessions, home):
    """Reset only a positively identified simulator; keep an inspectable backup."""
    from .competition import Competition
    from .db import Base, initialize
    from sqlalchemy import delete
    from uuid import uuid4
    with sessions() as s:
        record=s.get(Simulation,1);config=s.get(EventConfig,1)
        if not record or not config or config.server_address!=DemoApi.server_address:
            raise WorkflowError('Refusing to reset a database that is not the organiser simulator.')
        resources=deepcopy(record.data)
    backup=Path(home)/'backups';backup.mkdir(parents=True,exist_ok=True)
    (backup/(uuid4().hex+'.json')).write_text(json.dumps({'records':Competition(sessions).export(900,organiser=True),'simulator':resources},indent=2))
    with sessions.begin() as s:
        for table in reversed(Base.metadata.sorted_tables):s.execute(delete(table))
    initialize(sessions.kw['bind']);initialize_demo(sessions)
