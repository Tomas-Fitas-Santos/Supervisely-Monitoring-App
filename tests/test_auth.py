import importlib
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from monitoring.service import WorkflowError
from monitoring.db import EventConfig, EventMember, Team, database, initialize
from monitoring.service import Service
from monitoring.setup import Setup
from monitoring.connection import LocalConnection


@pytest.fixture
def ui(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCAL_DEVELOPMENT', 'true')
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/ui.db')
    monkeypatch.setenv('MONITORING_TEAM_ID', '10')
    monkeypatch.setenv('LOCAL_APP_HOME', str(tmp_path))
    module = importlib.import_module('monitoring.ui')
    engine, sessions = database(f'sqlite:///{tmp_path}/ui.db')
    initialize(engine)
    monkeypatch.setattr(module, 'sessions', sessions)
    monkeypatch.setattr(module, 'service', Service(sessions))
    monkeypatch.setattr(module, 'setup', Setup(sessions, 10))
    monkeypatch.setattr(module, 'connection', LocalConnection(sessions, tmp_path))
    monkeypatch.setattr(module, 'settings', replace(module.settings, local=False, home=tmp_path))
    return module


def test_missing_request_token_cannot_reuse_cached_user_api(ui):
    with pytest.raises(WorkflowError, match='Open this app'):
        ui.identity(NS(state=NS(api=None)))


def test_context_user_must_match_credential_owner(ui, monkeypatch):
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 91)
    with pytest.raises(WorkflowError, match='do not match'):
        ui.identity(NS(state=NS(api=api)))


def test_monitoring_membership_is_required(ui, monkeypatch):
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = None
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='member of the monitoring team'):
        ui.identity(NS(state=NS(api=api)))


def test_foreign_server_credential_is_rejected(ui, monkeypatch):
    api = Mock(server_address='https://another.example')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='different Supervisely server'):
        ui.identity(NS(state=NS(api=api)))


def test_live_local_identity_uses_token_owner_instead_of_demo_id(ui, monkeypatch):
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    monkeypatch.setenv('API_TOKEN', 'test-token')
    monkeypatch.setenv('LOCAL_USER_ID', '900')
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = NS(id=90, role='admin')
    monkeypatch.setattr(ui.connection, 'session_api', lambda secret: (api, 90) if secret == 'own-session' else (_ for _ in ()).throw(WorkflowError('Sign in')))
    uid, gateway = ui.identity(NS(client=NS(host='127.0.0.1'), cookies={'nightjar_session': 'own-session'}))
    assert uid == 90
    assert gateway.api.raw_api is api
    api.user.get_member_info_by_id.assert_called_once_with(10, 90)


def test_saved_admin_token_cannot_authenticate_a_browser_without_login(ui, monkeypatch):
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    monkeypatch.setenv('API_TOKEN', 'saved-admin-token')
    with pytest.raises(WorkflowError, match='Sign in'):
        ui.identity(NS(client=NS(host='127.0.0.1'), cookies={}))


def test_other_native_admin_cannot_manage_event_setup(ui):
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.owner_id, c.monitoring_team_id = 99, 10
    gateway = Mock()
    with pytest.raises(WorkflowError, match='Only the event organiser'):
        ui.setup.authorize(gateway, 90)


def test_setup_endpoint_denies_non_admin_before_loading_roster(ui, monkeypatch):
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(id=90, role='manager')
    monkeypatch.setattr(ui, 'identity', lambda request: (90, NS(api=api)))
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    catalog = Mock()
    monkeypatch.setattr(ui.SetupGateway, 'catalog', catalog)
    snapshot = Mock()
    monkeypatch.setattr(ui.setup, 'snapshot', snapshot)
    assert ui.setup_action(NS()) == {'ok': False}
    catalog.assert_not_called()
    snapshot.assert_not_called()
    api.team.create.assert_not_called()


def test_action_payload_is_read_from_current_request_not_shared_tab_state(ui):
    first = NS(state=NS(state={'dashboard': {'action': 'review', 'team_id': 1}}))
    second = NS(state=NS(state={'dashboard': {'action': 'release', 'team_id': 2}}))
    assert ui.request_form(first)['team_id'] == 1
    assert ui.request_form(second)['team_id'] == 2
    assert ui.request_form(first)['action'] == 'review'
    with pytest.raises(WorkflowError, match='Missing dashboard'):
        ui.request_form(NS(state=NS(state=None)))


@pytest.mark.parametrize('native_role', ['manager', 'annotator', 'admin'])
def test_native_membership_does_not_bypass_event_monitor_group(ui, monkeypatch, native_role):
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.owner_id, c.monitoring_team_id, c.annotator_team_id = 99, 10, 20
        s.add(EventMember(user_id=90, group='annotators', login='alice', name='Alice'))
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90)
    api.user.get_member_info_by_id.return_value = NS(role=native_role)
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='Monitors group'):
        ui.identity(NS(state=NS(api=api)))
    with ui.sessions.begin() as s:
        s.get(EventMember, 90).group = 'monitors'
    assert ui.identity(NS(state=NS(api=api)))[0] == 90


def test_fresh_hosted_event_claim_requires_launch_team_admin(ui, monkeypatch):
    monkeypatch.setattr(ui, 'setup', Setup(ui.sessions, 0))
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, launch_team_id=10))
    api = Mock(server_address=ui.settings.server_address)
    api.user.get_my_info.return_value = NS(id=90, login='organiser')
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 90)
    with pytest.raises(WorkflowError, match='Admin'):
        ui.identity(NS(state=NS(api=api)))
    api.user.get_member_info_by_id.return_value = NS(role='admin')
    assert ui.identity(NS(state=NS(api=api)))[0] == 90
    c = ui.event_config()
    assert c.owner_id == 90 and c.local_token is None
    api.user.get_my_info.return_value = NS(id=91, login='other')
    monkeypatch.setattr(ui.sly.env, 'user_from_multiuser_app', lambda: 91)
    with pytest.raises(WorkflowError, match='Only the organiser'):
        ui.identity(NS(state=NS(api=api)))


def test_connection_endpoint_is_local_only_and_rejects_foreign_origin(ui, monkeypatch):
    import asyncio
    request = NS(client=NS(host='127.0.0.1'), headers={}, base_url='http://127.0.0.1:8000/')
    assert asyncio.run(ui.connect(request)).status_code == 403

    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    request.headers['origin'] = 'https://foreign.example'
    assert asyncio.run(ui.connect(request)).status_code == 403


def test_monitors_can_read_directory_but_not_setup_operations(ui, monkeypatch):
    import supervisely.app
    data = {'dashboard': {'review_preview': None}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.owner_id, c.monitoring_team_id, c.annotator_team_id = 99, 10, 20
        s.add(EventMember(user_id=90, group='monitors', login='monitor', name='Monitor'))
        s.add(Team(id=101, name='Other team', monitor_id=91, annotator_ids=[11, 12]))
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    api.workspace.get_list.return_value = []
    monkeypatch.setattr(ui, 'identity', lambda request: (90, NS(api=api)))
    request = NS(state=NS(state={'dashboard': {'setup_action': 'overview'}}))
    assert ui.setup_action(request) == {'ok': True}
    assert data['dashboard']['directory']['teams'][0]['name'] == 'Other team'
    assert data['dashboard']['can_setup'] is False
    assert data['dashboard']['setup'] == {} and data['dashboard']['catalog'] == {}
    assert ui.service.snapshot(90)['teams'] == []
    request.state.state['dashboard']['setup_action'] = 'unassign'
    assert ui.setup_action(request) == {'ok': False}
    with ui.sessions() as s:
        assert s.get(Team, 101).monitor_id == 91


def test_preview_requires_own_assignment_before_remote_download(ui, monkeypatch):
    import supervisely.app
    from monitoring.db import Batch
    data = {'dashboard': {'review_preview': None}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    with ui.sessions.begin() as s:
        s.add(Team(id=101, name='Other team', monitor_id=91, annotator_ids=[11, 12]))
        s.flush()
        s.add(Batch(id='other', team_id=101, position=1, kind='images', dataset_id=50,
                    assets=[{'entity_id': 501, 'source_id': 'image'}]))
    gateway = Mock()
    monkeypatch.setattr(ui, 'identity', lambda request: (90, gateway))
    ui.action(NS(state=NS(state={'dashboard': {'action': 'preview_media', 'team_id': 101, 'batch_id': 'other',
                                              'entity_id': 501, 'frame_index': -1}})))
    gateway.review_preview.assert_not_called()


def test_removed_assignment_clears_cached_media(ui, monkeypatch):
    import supervisely.app
    data = {'dashboard': {'review_preview': {'team_id': 101, 'annotated': 'private-media'}}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    with ui.sessions.begin() as s:
        s.add(Team(id=101, name='Now assigned elsewhere', monitor_id=91, annotator_ids=[11, 12]))
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    ui.display_access(90, NS(api=api))
    assert data['dashboard']['review_preview'] is None


def test_manager_cannot_enable_organiser_oversight_and_cached_admin_data_is_cleared(ui,monkeypatch):
    import supervisely.app
    data={'dashboard':{'registration_rows':[{'email':'private'}], 'unit_page':{},'review_preview':None}}
    monkeypatch.setattr(supervisely.app,'DataJson',lambda:data)
    monkeypatch.setattr(ui.dashboard,'display',Mock())
    api=Mock();api.user.get_member_info_by_id.return_value=NS(role='manager',disabled=False)
    monkeypatch.setattr(ui,'identity',lambda req:(90,NS(api=api)))
    ui.action(NS(state=NS(state={'dashboard':{'action':'refresh','organiser_view':True}})))
    assert data['dashboard']['can_setup'] is False and data['dashboard']['registration_rows']==[]
    assert ui.dashboard.display.call_args.args[0]['organiser_view'] is False


def test_health_reports_database_failure_without_exposing_connection_details(ui, monkeypatch):
    monkeypatch.delenv('MONITORING_SIMULATOR', raising=False)
    assert ui.health() == {'status': 'ok', 'mode': 'production'}
    broken = Mock(side_effect=RuntimeError('postgresql://secret-password@private-host'))
    monkeypatch.setattr(ui, 'sessions', broken)
    response = ui.health()
    assert response.status_code == 503
    assert b'secret-password' not in response.body


def test_live_mode_rejects_simulator_presentation_actions_before_mutation(ui, monkeypatch):
    import supervisely.app
    import monitoring.demo
    data = {'dashboard': {}}
    monkeypatch.setattr(supervisely.app, 'DataJson', lambda: data)
    monkeypatch.setattr(ui.dashboard, 'display', Mock())
    monkeypatch.setattr(ui, 'identity', lambda request: (90, NS(api=Mock())))
    monkeypatch.setattr(ui.setup, 'authorize', lambda *args: None)
    monkeypatch.delenv('MONITORING_SIMULATOR', raising=False)
    prepare = Mock()
    monkeypatch.setattr(monitoring.demo, 'prepare_presentation', prepare)
    assert ui.setup_action(NS(state=NS(state={'dashboard': {'setup_action': 'demo_prepare'}}))) == {'ok': False}
    prepare.assert_not_called()


def test_legacy_team_form_cannot_import_before_shared_team_is_configured(ui, monkeypatch):
    import supervisely.app
    from monitoring.demo import DemoApi, initialize_demo
    from monitoring.gateway import Gateway
    data={'dashboard':{}}
    monkeypatch.setattr(supervisely.app,'DataJson',lambda:data)
    monkeypatch.setattr(ui.dashboard,'display',Mock())
    monkeypatch.setattr(ui,'settings',replace(ui.settings,local=True))
    initialize_demo(ui.sessions)
    api=DemoApi(ui.sessions,900,ui.settings.home)
    monkeypatch.setattr(ui,'identity',lambda request:(900,Gateway(api,ui.sessions)))
    form={'setup_action':'email_team','name':'Manual email team',
          'participant_emails':['tomas.gaming.21@gmail.com'], 'monitor_email':'tomas.bf.santos@gmail.com'}
    assert ui.setup_action(NS(state=NS(state={'dashboard':form}))) == {'ok':False}
    assert not data['dashboard']['directory']['teams']


def test_two_browser_logins_keep_admin_and_monitor_models_separate(ui, monkeypatch):
    from fastapi.testclient import TestClient
    from monitoring.local_shell import LocalShell
    monkeypatch.setattr(ui, 'settings', replace(ui.settings, local=True))
    apis = {}
    for uid, login in [(99, 'admin'), (90, 'monitor')]:
        api = Mock(server_address=ui.settings.server_address)
        api.user.get_my_info.return_value = NS(id=uid, login=login)
        api.user.get_member_info_by_id.return_value = NS(id=uid, login=login, role='admin' if uid == 99 else 'manager', disabled=False)
        apis[login + '-token'] = api
    monkeypatch.setattr(ui.sly, 'Api', lambda server, token: apis[token])
    shell = LocalShell(ui.app, ui.dashboard, ui.connection.session)
    admin = TestClient(shell, base_url='http://127.0.0.1')
    monitor = TestClient(shell, base_url='http://127.0.0.1')
    anonymous = TestClient(shell, base_url='http://127.0.0.1')
    login = admin.post('/connection', json={'server':ui.settings.server_address,'token':'admin-token','user_id':99})
    assert login.status_code == 200, login.text
    assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
    assert 'admin-token' not in login.text
    with ui.sessions.begin() as s:
        c = s.get(EventConfig, 1)
        c.monitoring_team_id = c.annotator_team_id = 10
        c.source_workspace_id = 20
        s.add(EventMember(user_id=90, login='monitor', name='Monitor', group='monitors'))
        s.add(Team(id=101, name='Assigned to monitor', monitor_id=90, annotator_ids=[11,12]))
    assert monitor.post('/connection', json={'server':ui.settings.server_address,'token':'monitor-token','user_id':90}).status_code == 200
    for client, login_name in ((admin, 'admin'), (monitor, 'monitor')):
        api = apis[login_name + '-token']
        api.user.get_my_info.reset_mock()
        api.user.get_member_info_by_id.reset_mock()
        response = client.post('/dashboard/action', json={'state':{'dashboard':{'action':'refresh'}},'context':{}},
                               headers={'x-nightjar-view':'1', 'x-user-id':'99'})
        assert response.status_code == 200
        view = response.json()['dashboard']
        assert view['connection']['login'] == login_name
        assert view['can_setup'] == (login_name == 'admin')
        api.user.get_my_info.assert_called_once()
        api.user.get_member_info_by_id.assert_called_once_with(10, 99 if login_name == 'admin' else 90)
    admin_data = admin.post('/sly/data',json={}).json()['dashboard']
    monitor_data = monitor.post('/sly/data?userId=99',json={},headers={'x-user-id':'99'}).json()['dashboard']
    assert admin_data['can_setup'] and admin_data['connection']['login'] == 'admin'
    assert not monitor_data['can_setup'] and monitor_data['connection']['login'] == 'monitor'
    assert monitor_data['snapshot']['teams'][0]['name'] == 'Assigned to monitor'
    assert anonymous.post('/sly/data',json={},headers={'x-user-id':'99'}).status_code == 401
    assert anonymous.post('/dashboard/action',json={'state':{'dashboard':{'action':'refresh'}}},
                          headers={'x-nightjar-view':'1','x-user-id':'99'}).status_code == 401
    assert monitor.post('/setup/action',json={'state':{'dashboard':{'setup_action':'reset_preview','reset_scope':'settings'}}}).json() == {'ok':False}
    assert monitor.post('/logout').json() == {'ok':True}
    assert monitor.post('/sly/data',json={}).status_code == 401
    assert admin.post('/sly/data',json={}).json()['dashboard']['can_setup']


def test_csv_upload_automatically_maps_columns_and_populates_people_without_inviting(ui, monkeypatch):
    import supervisely.app
    data={'dashboard':{}}
    monkeypatch.setattr(supervisely.app,'DataJson',lambda:data)
    monkeypatch.setattr(ui.dashboard,'display',Mock())
    monkeypatch.setattr(ui.setup,'authorize',Mock())
    api=Mock()
    api.user.get_my_info.return_value=NS(id=99,login='admin')
    monkeypatch.setattr(ui,'identity',lambda request:(99,NS(api=api)))
    with ui.sessions.begin() as session:
        config=session.get(EventConfig,1)
        config.owner_id=99
        config.monitoring_team_id=config.annotator_team_id=10
    csv='team_name,member_1_email,member_2_email,monitor_email\nPair,a@example.org,b@example.org,monitor@example.org\n'
    result=ui.setup_action(NS(state=NS(state={'dashboard':{'setup_action':'registration_import','csv_text':csv}})))
    assert result['ok'] is True
    people=data['dashboard']['registration_people']
    assert len(people)==3 and {p['status'] for p in people}=={'not_invited'}
    assert next(p for p in people if p['email']=='monitor@example.org')['role']=='monitor'
    api.post.assert_not_called()


def test_upload_chunk_returns_offset_without_rebuilding_the_dashboard(ui, monkeypatch):
    monkeypatch.setattr(ui, 'identity', lambda request: (99, NS(api=Mock())))
    monkeypatch.setattr(ui.setup, 'authorize', Mock())
    stage = Mock(return_value=32)
    monkeypatch.setattr(ui.setup, 'stage', stage)
    display = Mock()
    monkeypatch.setattr(ui.dashboard, 'display', display)
    access = Mock()
    monkeypatch.setattr(ui, 'display_access', access)
    request = NS(headers={'x-nightjar-view':'0'}, state=NS(state={'dashboard':{
        'setup_action':'chunk', 'upload_id':'upload-123', 'filename':'a.jpg',
        'size':32, 'offset':0, 'chunk':'encoded', 'relative_path':'images/a.jpg'}}))
    assert ui.setup_action(request) == {'ok':True, 'upload_offset':32}
    stage.assert_called_once_with(99, 'upload-123', 'a.jpg', 32, 0, 'encoded', 'images/a.jpg')
    display.assert_not_called()
    access.assert_not_called()
