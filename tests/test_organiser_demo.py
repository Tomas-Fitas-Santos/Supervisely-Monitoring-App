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
    r.import_csv(900,(ROOT/'registrations.csv').read_text(),{k:k for k in COLUMNS});r.resolve(gw,900)
    for i,row in enumerate(r.rows()):r.provision(gw,setup,900,row['id'],900 if i%2==0 else 901)
    paths=[(p.relative_to(ROOT/'dataset').as_posix(),p) for p in sorted((ROOT/'dataset'/'images').rglob('*.jpg'))]
    result=gw.upload(10,30,'Birds','images',paths,pilot_meta(),lambda info:None)
    comp=Competition(service.sessions);config=comp.config();comp.save_rules(900,config['revision'],config['rules'],True)
    pid,preview=setup.preview(gw,900,result['dataset_ids'],[t['id'] for t in setup.roster()],3,1,1,True,False)
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
    assert len(setup.roster())==3 and len(Registrations(service.sessions).rows())==3
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
    assert len(setup.roster())==3
