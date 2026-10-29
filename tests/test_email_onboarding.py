from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select

from monitoring import controller
from monitoring.competition import Competition
from monitoring.db import Batch, Registration, SetupState, Team, TeamOnboarding
from monitoring.demo import DemoApi, initialize_demo
from monitoring.onboarding import EmailOnboarding
from monitoring.pilot import accounts, import_roster, upload_photos
from monitoring.registration import COLUMNS, Registrations
from monitoring.service import WorkflowError
from monitoring.setup import Setup
from monitoring.setup_gateway import InvitationRejected, SetupGateway


@pytest.fixture
def event(service, tmp_path):
    with service.sessions.begin() as session:
        session.execute(delete(Batch)); session.execute(delete(Team))
    initialize_demo(service.sessions)
    api = DemoApi(service.sessions, 900, tmp_path)
    gateway = SetupGateway(api, service.sessions)
    setup = Setup(service.sessions, 10, tmp_path / 'uploads')
    import_roster(service.sessions, 900)
    return service, api, gateway, setup, EmailOnboarding(service.sessions)


def created(event, accept=False):
    service, api, gateway, setup, onboarding = event
    ids = []
    for row in Registrations(service.sessions).rows():
        ids.append(onboarding.provision(gateway, setup, 900, row['id']))
    if accept:
        for tid in ids: api.accept_invitations(tid)
        onboarding.refresh(gateway, setup, 900)
    return ids


def test_requested_two_teams_import_with_one_annotator_and_shared_manager(event):
    service, api, gateway, setup, onboarding = event
    rows = Registrations(service.sessions).rows()
    assert [r['team_name'] for r in rows] == ['TEAM 1', 'TEAM 2']
    assert [r['members'][0]['email'] for r in rows] == accounts()['participant_emails']
    assert all(len(r['members']) == 1 and r['monitor_email'] == accounts()['organiser_email'] for r in rows)
    assert all(not r['team_id'] for r in rows)


def test_email_invites_create_visible_pending_teams_without_user_search(event, monkeypatch):
    service, api, gateway, setup, onboarding = event
    monkeypatch.setattr(api.user, 'get_list', Mock(side_effect=AssertionError('Global lookup forbidden')))
    original = api.post
    calls = []
    def post(method, data, **kwargs):
        calls.append((method, dict(data)))
        return original(method, data, **kwargs)
    monkeypatch.setattr(api, 'post', post)
    tids = created(event)
    directory = setup.directory()
    assert len(directory['teams']) == 2
    assert all(t['membership_status'] == 'pending' and not t['annotator_ids'] for t in directory['teams'])
    assert all(t['monitor_id'] == 900 and t['participants'][0]['status'] == 'pending' for t in directory['teams'])
    assert setup.snapshot()['blocked_by'] is None
    additions = [payload for method, payload in calls if method == 'members.add']
    assert [p['login'] for p in additions] == accounts()['participant_emails']
    assert all(p['roleId'] == 5 for p in additions)
    # The native Admin already owns both teams and the monitoring group. It is
    # never downgraded or re-invited just to label its event responsibility Manager.
    assert all(api.user.get_member_info_by_id(tid, 900).role == 'admin' for tid in tids)
    before = len(additions), len(api.team.get_list())
    created(event)
    onboarding.refresh(gateway, setup, 900)
    assert (len([1 for method, _ in calls if method == 'members.add']), len(api.team.get_list())) == before
    assert setup.snapshot()['blocked_by'] is None


def test_pending_blocks_distribution_then_acceptance_unlocks_even_unique_image_allocation(event):
    service, api, gateway, setup, onboarding = event
    tids = created(event)
    sources = upload_photos(setup, gateway, 900, 'upload_two_bird_images')
    config = Competition(service.sessions).config()
    Competition(service.sessions).save_rules(900, config['revision'], dict(config['rules'], replicas=1), True)
    with pytest.raises(WorkflowError, match='invitations'):
        setup.preview(gateway, 900, sources['dataset_ids'], tids, 1, 1, 1, True, False)
    for tid in tids: api.accept_invitations(tid)
    onboarding.refresh(gateway, setup, 900)
    rows = Registrations(service.sessions).rows()
    assert all(r['membership_status'] == 'ready' and r['ready'] for r in rows)
    for row in rows:
        assert [e['effective_role'] for e in row['memberships'] if e['scope']=='team'] == ['annotator', 'admin']
    plan, preview = setup.preview(gateway, 900, sources['dataset_ids'], tids, 1, 1, 1, True, False)
    assert [team['images'] for team in preview['teams']] == [1, 1]
    assert preview['workload_spread'] == 0 and preview['source_videos'] == 0
    assert set(preview['coverage'].values()) == {1}
    setup.distribute(gateway, 900, plan)
    with service.sessions() as session:
        batches = list(session.scalars(select(Batch).order_by(Batch.team_id)))
        teams = {t.id: t for t in session.scalars(select(Team))}
    assert len(batches) == 2
    assert len({b.assets[0]['source_id'] for b in batches}) == 2
    assert len({b.assets[0]['entity_id'] for b in batches}) == 2
    for batch in batches:
        controller.release(service, gateway, 900, batch.team_id, batch.id, teams[batch.team_id].revision)
        _, released = service.spec(900, batch.team_id, batch.id)
        assert len(released.job_ids) == 1
        job = api.labeling_job.get_info_by_id(released.job_ids[0])
        assert job.assigned_to_id == teams[batch.team_id].annotator_ids[0]
        assert job.reviewer_id == 900 and job.images_count == 1
        assert job.team_id == batch.team_id


def test_refresh_does_not_mark_existing_user_id_accepted_before_invitation_acceptance(event):
    service, api, gateway, setup, onboarding = event
    created(event)
    for row in Registrations(service.sessions).rows():
        membership = row['memberships'][0]
        assert membership['user_id'] and membership['status'] == 'pending'
        assert not row['ready']
    onboarding.refresh(gateway, setup, 900)
    assert not any(t['annotator_ids'] for t in setup.roster())


def test_membership_loss_is_detected_before_distribution_even_without_cached_refresh(event):
    service, api, gateway, setup, onboarding = event
    tids = created(event, accept=True)
    uid = setup.roster()[0]['annotator_ids'][0]
    api.user.remove_from_team(uid, tids[0])
    with pytest.raises(WorkflowError, match='changed'):
        onboarding.verify_ready(gateway, tids[0])
    onboarding.refresh(gateway, setup, 900)
    row = next(r for r in Registrations(service.sessions).rows() if r['team_id']==tids[0])
    assert row['membership_status'] == 'needs_attention' and not row['ready']


def test_wrong_native_role_does_not_unlock_distribution(event):
    service, api, gateway, setup, onboarding = event
    tids = created(event, accept=True)
    uid = setup.roster()[0]['annotator_ids'][0]
    api.user.change_team_role(uid, tids[0], 3)
    onboarding.refresh(gateway, setup, 900)
    row = next(r for r in Registrations(service.sessions).rows() if r['team_id']==tids[0])
    assert row['memberships'][0]['status']=='role_mismatch' and not row['ready']


def test_definitive_invitation_failure_keeps_team_visible_and_other_setup_available(event, monkeypatch):
    service, api, gateway, setup, onboarding = event
    original = gateway.invite_email
    monkeypatch.setattr(gateway, 'invite_email', Mock(side_effect=InvitationRejected('Native seat limit reached.')))
    tids = created(event)
    rows = Registrations(service.sessions).rows()
    assert len(set(tids)) == 2 and all(r['team_id'] for r in rows)
    assert all(r['membership_status']=='needs_attention' and 'seat limit' in r['error'] for r in rows)
    assert setup.snapshot()['blocked_by'] is None
    monkeypatch.setattr(gateway, 'invite_email', original)
    created(event)
    assert all(r['membership_status']=='pending' for r in Registrations(service.sessions).rows())
    assert len(setup.roster()) == 2


def test_unknown_remote_write_preserves_team_and_blocks_blind_recreation(event, monkeypatch):
    service, api, gateway, setup, onboarding = event
    monkeypatch.setattr(gateway, 'invite_email', Mock(side_effect=TimeoutError('private-token')))
    row = Registrations(service.sessions).rows()[0]
    with pytest.raises(WorkflowError, match='needs inspection') as error:
        onboarding.provision(gateway, setup, 900, row['id'])
    assert 'private-token' not in str(error.value)
    assert len(setup.roster()) == 1 and Registrations(service.sessions).rows()[0]['team_id']
    with pytest.raises(WorkflowError, match='inspection'):
        onboarding.provision(gateway, setup, 900, row['id'])
    assert len(setup.roster()) == 1


def test_read_failure_becomes_unknown_without_resending_or_locking_setup(event, monkeypatch):
    service, api, gateway, setup, onboarding = event
    created(event, accept=True)
    monkeypatch.setattr(gateway, 'invitation_members', Mock(side_effect=PermissionError('native permission')))
    onboarding.refresh(gateway, setup, 900)
    assert all(r['membership_status']=='needs_attention' for r in Registrations(service.sessions).rows())
    assert setup.snapshot()['blocked_by'] is None


def test_new_onboarding_records_survive_idempotent_csv_import_and_are_exported(event):
    service, api, gateway, setup, onboarding = event
    tids = created(event, accept=True)
    import_roster(service.sessions, 900)
    assert len(setup.roster()) == 2
    assert all(r['membership_status']=='ready' for r in Registrations(service.sessions).rows())
    assert len(Competition(service.sessions).export(900, organiser=True)['onboarding']) == 2
    assert Competition(service.sessions).export(900)['onboarding'] == []


def test_monitor_handover_updates_invitation_identity_and_preserves_original_csv_import(event):
    service,api,gateway,setup,onboarding=event
    tid=created(event,accept=True)[0]
    current=next(t for t in setup.roster() if t['id']==tid)
    setup.assign_monitor(gateway,900,'handover_new_email_team',tid,901,current['revision'])
    onboarding.verify_ready(gateway,tid)
    with service.sessions() as session:
        registration=session.scalar(select(Registration).where(Registration.team_id==tid))
        plan=session.get(TeamOnboarding,registration.id)
        assert registration.monitor_id==901
        assert {e['user_id'] for e in plan.memberships if e['requested_role']=='manager'}=={901}
    import_roster(service.sessions,900)
    assert next(t for t in setup.roster() if t['id']==tid)['monitor_id']==901


def test_video_remains_a_whole_asset_for_one_annotator_teams():
    from monitoring.planner import allocate
    video={'source_id':'full-clip','kind':'video','units':120,'frames':120,'entity_id':7}
    result=allocate([1,2],[video],replicas=2,batch_units=1)
    assert all(item['videos']==[video] and not item['image_batches'] for item in result.values())


def test_two_annotator_registration_is_still_supported(event):
    service, api, gateway, setup, onboarding = event
    with service.sessions.begin() as session:
        session.execute(delete(TeamOnboarding));session.execute(delete(Registration))
    csv='team_name,monitor_email,member_1_email,member_2_email\nPair,tomas.bf.santos@gmail.com,tomas.gaming.21@gmail.com,tomas.santos.work2002@gmail.com\n'
    Registrations(service.sessions).import_csv(900,csv,{field:field for field in COLUMNS})
    row=Registrations(service.sessions).rows()[0]
    tid=onboarding.provision(gateway,setup,900,row['id'])
    api.accept_invitations(tid);onboarding.refresh(gateway,setup,900)
    assert len(setup.roster()[0]['annotator_ids'])==2


def test_native_raw_membership_list_uses_accepted_filter_even_if_activated_is_omitted():
    api=Mock()
    accepted={'id':1,'login':'admin','role':'admin','email':'admin@example.test'}
    pending={'id':2,'login':'pending','role':'annotator','inviteEmail':'pending@example.test'}
    api.post.side_effect=[NS(json=lambda:{'entities':[accepted,pending],'pagesCount':1}),
                          NS(json=lambda:{'entities':[accepted],'pagesCount':1})]
    members=SetupGateway(api).invitation_members(10)
    assert [m['membership_active'] for m in members]==[True,False]
    assert api.post.call_args_list[1].args[1]['showInvited'] is False


def test_email_invite_uses_native_identifier_without_global_account_lookup():
    api=Mock();api.role.get_list.return_value=[NS(id=5,role='annotator')]
    api.post.return_value.json.return_value={'userId':33}
    assert SetupGateway(api).invite_email(10,'participant@example.test','annotator')=={'user_id':33}
    api.post.assert_called_once_with('members.add',{'teamId':10,'login':'participant@example.test','roleId':5},retries=1)
    api.user.get_list.assert_not_called()
