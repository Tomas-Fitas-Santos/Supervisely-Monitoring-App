from pathlib import Path
import hashlib,json
import pytest
from sqlalchemy import select,delete
from monitoring import controller
from monitoring.competition import Competition
from monitoring.db import Base, Batch, Team, Simulation, TaskAttempt, EventClosure, database, initialize
from monitoring.demo import initialize_demo, DemoApi, ROOT, submit_demo
from monitoring.event_actions import corrections, close_event
from monitoring.registration import Registrations, COLUMNS
from monitoring.setup import Setup, pilot_meta
from monitoring.setup_gateway import SetupGateway
from monitoring.service import Service, WorkflowError


@pytest.fixture
def demo(service,tmp_path):
    # Use the same disposable PostgreSQL/SQLite database as the rest of CI.
    from monitoring.db import EventConfig,EventMember
    with service.sessions.begin() as s:s.execute(delete(Batch));s.execute(delete(Team))
    initialize_demo(service.sessions)
    api=DemoApi(service.sessions,900,tmp_path);gw=SetupGateway(api,service.sessions);setup=Setup(service.sessions,10,tmp_path/'staging')
    return service,api,gw,setup


def prepare(demo):
    service,api,gw,setup=demo;r=Registrations(service.sessions)
    # Preserve coverage of the ordinary two-annotator event pair separately
    # from the requested demo's two one-annotator teams.
    import csv,io
    from monitoring.pilot import accounts
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=COLUMNS);writer.writeheader()
    writer.writerow({'team_name':'Legacy pair fixture','member_1_email':accounts()['participant_emails'][0],
                     'member_2_email':accounts()['participant_emails'][1]})
    r.import_csv(900,stream.getvalue(),{k:k for k in COLUMNS});r.resolve(gw,900)
    for i,row in enumerate(r.rows()):r.provision(gw,setup,900,row['id'],900 if i%2==0 else 901)
    paths=[(p.relative_to(ROOT/'dataset').as_posix(),p) for p in sorted((ROOT/'dataset'/'images').rglob('*.jpg'))]
    result=gw.upload(10,30,'Birds','images',paths,pilot_meta(),lambda info:None)
    comp=Competition(service.sessions);config=comp.config();comp.save_rules(900,config['revision'],config['rules'],True)
    pid,preview=setup.preview(gw,900,result['dataset_ids'],[t['id'] for t in setup.roster()],1,1,1,True,False)
    setup.distribute(gw,900,pid)
    with service.sessions() as s:
        t=s.scalar(select(Team).where(Team.monitor_id==900));b=s.scalar(select(Batch).where(Batch.team_id==t.id).order_by(Batch.position))
    controller.release(service,gw,900,t.id,b.id,t.revision)
    t,b=service.spec(900,t.id,b.id);submit_demo(api,b);controller.sync(service,gw,t,b)
    sid=comp.refresh(gw,t,b)
    return comp,t,b,sid


def test_bundled_photos_are_real_decodable_cc0_and_have_unique_paths():
    from PIL import Image
    credits=json.loads((ROOT/'image-credits.json').read_text())
    assert len(credits)>=2
    for row in credits:
        path=ROOT/row['path']
        assert row['license']=='CC0' and 'commons.wikimedia.org' in row['source']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
        with Image.open(path) as image:assert image.width>=640
    assert len({row['path'] for row in credits})==len(credits)


def test_complete_real_photo_demo_and_persistent_restart(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    u=comp.page(900,b.id)['units'][0]
    with service.sessions() as s:snap=s.get(__import__('monitoring.db',fromlist=['Submission']).Submission,sid)
    preview=gw.review_preview(t,b,u['entity_id'],-1,snapshot=snap.payload)
    assert preview['annotated'].startswith('data:image/jpeg;base64,') and preview['labels']==4
    comp.assess(900,b.id,sid,u['entity_id'],-1,'unit','whole','good')
    assert next(r for r in comp.leaderboard() if r['team_id']==t.id)['points']==1
    initialize_demo(service.sessions)
    assert len(setup.roster())==1 and len(Registrations(service.sessions).rows())==1
    assert comp.export(900)['submissions']


def test_correction_jobs_preserve_native_ownership_and_snapshot_history(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo;unit=comp.page(900,b.id)['units'][0]
    comp.assess(900,b.id,sid,unit['entity_id'],-1,'unit','whole','bad','Correct the beak tip.')
    t,b=service.spec(900,t.id,b.id);old=list(b.job_ids)
    corrections(service,setup,gw,900,b.id,t.revision)
    t,new=service.spec(900,t.id,b.id)
    assert new.job_ids!=old and api.labeling_job.get_info_by_id(old[0]).status=='stopped'
    assert 'beak' in api.labeling_job.get_info_by_id(new.job_ids[0]).description
    assert comp.page(900,b.id)['version']==1
    submit_demo(api,new);controller.sync(service,gw,t,new);comp.refresh(gw,t,new)
    assert comp.page(900,b.id)['version']==2 and comp.page(900,b.id)['stats']['pending']>0
    with service.sessions() as s:assert s.scalar(select(TaskAttempt)).parent_job_ids==old


def test_active_monitor_handover_updates_jobs_and_revokes_old_app_access(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    t,b=service.spec(900,t.id,b.id);setup.assign_monitor(gw,900,'native_handover',t.id,901,t.revision)
    assert all(api.labeling_job.get_info_by_id(j).reviewer_id==901 for j in b.job_ids)
    with pytest.raises(WorkflowError,match='assigned'):comp.page(900,b.id)
    assert comp.page(901,b.id)['submission_id']==sid


def test_closure_stops_jobs_captures_and_prevents_further_releases(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    close_event(service,setup,gw,900)
    with service.sessions() as s:assert s.get(EventClosure,1)
    assert all(api.labeling_job.get_info_by_id(j).status=='stopped' for j in b.job_ids)
    with service.sessions() as s:
        current=s.get(Team,t.id);nxt=s.scalar(select(Batch).where(Batch.team_id==t.id,Batch.state=='locked').order_by(Batch.position))
    with pytest.raises(WorkflowError,match='closed'):service.reserve_release(900,t.id,nxt.id,current.revision)
    close_event(service,setup,gw,900) # Idempotent close does not create jobs or snapshots.


def test_native_acceptance_requires_all_criteria_and_unchanged_capture(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo;unit=comp.page(900,b.id)['units'][0]
    with pytest.raises(WorkflowError,match='every required'):controller.approve(service,setup,gw,900,t.id,b.id,service.spec(900,t.id,b.id)[0].revision,'Checked')
    comp.assess(900,b.id,sid,unit['entity_id'],-1,'unit','whole','good')
    t,b=service.spec(900,t.id,b.id)
    controller.approve(service,setup,gw,900,t.id,b.id,t.revision,'Inspected the complete submitted batch.')
    assert service.spec(900,t.id,b.id)[1].state=='approved'
    assert api.labeling_job.get_info_by_id(b.job_ids[0]).status=='completed'


def test_native_acceptance_rejects_annotations_edited_after_judgment(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo;unit=comp.page(900,b.id)['units'][0]
    comp.assess(900,b.id,sid,unit['entity_id'],-1,'unit','whole','good')
    media=api.image.get_info_by_id(unit['entity_id']);annotation=media.annotation;annotation['objects']=[]
    api.store.edit('media',unit['entity_id'],{'annotation':annotation})
    t,b=service.spec(900,t.id,b.id)
    with pytest.raises(WorkflowError,match='changed after'):controller.approve(service,setup,gw,900,t.id,b.id,t.revision,'Checked')
    assert api.labeling_job.get_info_by_id(b.job_ids[0]).status=='on_review'


def test_organiser_adjudication_records_the_actual_actor(demo):
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    t,b=service.spec(900,t.id,b.id);setup.assign_monitor(gw,900,'handover_for_judging',t.id,901,t.revision)
    assert comp.page(900,b.id,organiser=True)['submission_id']==sid
    unit=comp.page(900,b.id,organiser=True)['units'][0]
    comp.assess(900,b.id,sid,unit['entity_id'],-1,'unit','whole','good',adjudicate=True)
    from monitoring.db import Audit
    with service.sessions() as s:
        audit=s.scalar(select(Audit).where(Audit.action=='adjudication'))
        assert audit.actor_id==900 and audit.team_id==t.id


def test_demo_reset_is_isolated_backed_up_and_can_rehearse_again(demo):
    from monitoring.demo import reset_demo
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    reset_demo(service.sessions,api.home)
    assert setup.roster()==[] and Registrations(service.sessions).rows()==[]
    assert list((api.home/'backups').glob('*.json'))
    assert not Competition(service.sessions).config()['frozen']
    prepare(demo)
    assert len(setup.roster())==1


def test_video_handover_is_limited_to_the_pair_and_keeps_one_native_owner(demo):
    from monitoring.event_actions import video_handover
    from monitoring.gateway import Gateway
    comp,t,b,sid=prepare(demo);service,api,gw,setup=demo
    workspace=api.workspace.get_list(t.id)[0]
    project=api.project.create(workspace.id,'Video ownership pilot',type='videos')
    dataset=api.dataset.create(project.id,'Complete video')
    native=api.store.add('media',{'kind':'video','name':'clip.mp4','dataset_id':dataset.id,'project_id':project.id,'frames_count':3})
    jobs=api.labeling_job.create(name='video handover fixture',dataset_id=dataset.id,user_ids=[t.annotator_ids[0]],images_ids=[native.id],reviewer_id=900)
    with service.sessions.begin() as s:
        s.add(Batch(id='whole_video',team_id=t.id,position=99,kind='video',dataset_id=dataset.id,
            assets=[{'source_id':'video-fixture','entity_id':native.id,'frames':3,'assignee_id':t.annotator_ids[0]}],
            state='active',job_ids=[jobs[0].id],remote_status='in_progress'))
    current,video=service.spec(900,t.id,'whole_video')
    with pytest.raises(WorkflowError,match='participants'):
        video_handover(service,setup,gw,900,video.id,current.revision,99999)
    assert api.labeling_job.get_info_by_id(jobs[0].id).assigned_to_id==t.annotator_ids[0]
    video_handover(service,setup,gw,900,video.id,current.revision,t.annotator_ids[1])
    current,video=service.spec(900,t.id,'whole_video')
    assert api.labeling_job.get_info_by_id(jobs[0].id).assigned_to_id==t.annotator_ids[1]
    assert Gateway.slices(current,video)==[(t.annotator_ids[1],[native.id])]


def test_supplied_emails_and_two_native_accounts_receive_work_across_small_batches(demo):
    from monitoring.gateway import Gateway
    from monitoring.pilot import accounts
    comp, team, batch, _ = prepare(demo)
    service, api, gateway, setup = demo
    assert api.user.get_my_info().email == 'tomas.bf.santos@gmail.com'
    participants = {api.user.get_info_by_id(uid).email for uid in team.annotator_ids}
    assert participants == set(accounts()['participant_emails'])
    with service.sessions() as session:
        batches = list(session.scalars(select(Batch).where(Batch.team_id == team.id).order_by(Batch.position)))
    assert len(batches) == 2
    owners = [uid for b in batches for uid, _ in Gateway.slices(team, b)]
    assert owners == team.annotator_ids
    # Release the next one-image batch after the first submission; the second
    # member receives a real-shaped native job instead of always the first member.
    current, upcoming = service.spec(900, team.id, batches[1].id)
    controller.release(service, gateway, 900, team.id, upcoming.id, current.revision)
    _, released = service.spec(900, team.id, upcoming.id)
    assert api.labeling_job.get_info_by_id(released.job_ids[0]).assigned_to_id == team.annotator_ids[1]


def test_presentation_shortcut_builds_submitted_and_upcoming_work_without_network(demo):
    from monitoring.demo import prepare_presentation
    service, api, gateway, setup = demo
    prepare_presentation(service.sessions, api.home)
    team = setup.roster()[0]
    with service.sessions() as session:
        batches = list(session.scalars(select(Batch).where(Batch.team_id==team['id']).order_by(Batch.position)))
        assert len(list(session.scalars(select(Team)))) == 2
    assert len(batches) == 2 and batches[1].state == 'locked'
    page = Competition(service.sessions).page(900, batches[0].id)
    assert page['submission_id'] and page['stats']['pending'] > 0
    assert Competition(service.sessions).config()['frozen']
    with pytest.raises(WorkflowError, match='Continue the existing demo'):
        prepare_presentation(service.sessions, api.home)


def test_bundled_live_upload_uses_configured_workspace_and_keeps_source_checksums(demo):
    from monitoring.pilot import upload_photos
    from monitoring.db import EventConfig, SourceOrigin
    service, api, gateway, setup = demo
    workspace = api.workspace.create(10, 'Different configured workspace')
    with service.sessions.begin() as session:
        session.get(EventConfig, 1).source_workspace_id = workspace.id
    result = upload_photos(setup, gateway, 900, 'upload_bundled_photos')
    assert result['dataset_ids']
    for project in api.project.get_list(workspace.id):
        assert project.workspace_id == workspace.id
    with service.sessions() as session:
        assert len(list(session.scalars(select(SourceOrigin)))) == 2
    with pytest.raises(WorkflowError, match='Source media is already available'):
        upload_photos(setup, gateway, 900, 'dont_duplicate_photos')


def test_live_sample_setup_initializes_pending_teams_and_photos_without_simulating_work(demo):
    from monitoring.db import EventConfig, EventMember
    from monitoring.pilot import prepare_sample
    service, api, gateway, _ = demo
    with service.sessions.begin() as session:
        config = session.get(EventConfig, 1)
        config.monitoring_team_id = config.annotator_team_id = config.source_workspace_id = None
        session.execute(delete(EventMember))
    setup = Setup(service.sessions, 0)
    prepare_sample(setup, gateway, 900, 'live_sample_setup')
    assert setup.groups()['ready']
    assert {r['team_name'] for r in Registrations(service.sessions).rows()} == {'TEAM 1', 'TEAM 2'}
    assert all(r['membership_status'] == 'pending' for r in Registrations(service.sessions).rows())
    sources = gateway.sources(setup.monitoring_team_id)
    assert sum(d['items'] for d in sources['datasets']) == 2
    before = api.store.read()
    prepare_sample(setup, gateway, 900, 'repeat_live_sample')
    after = api.store.read()
    assert before['teams'] == after['teams'] and before['media'] == after['media']
    assert before['invitations'] == after['invitations']
    assert not after['jobs']
    assert not Competition(service.sessions).config()['frozen']
    assert not any(t['has_batches'] for t in setup.roster())


def test_live_sample_setup_retains_recovery_lock_and_never_resets_a_closed_event(demo):
    from monitoring.db import SetupState
    from monitoring.pilot import prepare_sample
    service, api, gateway, setup = demo
    with service.sessions.begin() as session:
        session.get(SetupState, 1).operation_id = 'unresolved_native_write'
    before = api.store.read()
    with pytest.raises(WorkflowError, match='needs inspection'):
        prepare_sample(setup, gateway, 900, 'blocked_sample_setup')
    assert before == api.store.read()
    with service.sessions.begin() as session:
        session.get(SetupState, 1).operation_id = None
        session.add(EventClosure(id=1, operation_id='closed_for_test'))
    with pytest.raises(WorkflowError, match='closed'):
        prepare_sample(setup, gateway, 900, 'closed_sample_setup')
    assert before == api.store.read()
