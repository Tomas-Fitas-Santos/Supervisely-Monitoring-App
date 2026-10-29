from copy import deepcopy
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select
from monitoring.competition import Competition, annotation_units
from monitoring.db import Assessment, Audit, Batch, CompetitionConfig, EventClosure, Submission, Team
from monitoring.service import WorkflowError


def labels():
    return {'size':{'height':100,'width':100},'tags':[], 'objects':[
        {'classTitle':'bird','tags':[{'name':'bird_id','value':'b1'}], 'points':{'exterior':[[1,1],[90,90]],'interior':[]}},
        {'classTitle':'beak','tags':[{'name':'bird_id','value':'b1'},{'name':'visibility','value':'visible'}], 'points':{'exterior':[[50,50]],'interior':[]}}]}


class Reader:
    def __init__(self): self.payload={'meta':{},'annotations':{'101':labels(),'102':labels()}};self.status='on_review'
    def progress(self,batch): return {'status':self.status,'completed':2,'total':2}
    def capture_annotations(self,team,batch): return deepcopy(self.payload)


def capture(service, gw=None, **rules):
    comp=Competition(service.sessions); config=comp.config()
    comp.save_rules(90,config['revision'],{**config['rules'],**rules},True)
    with service.sessions.begin() as s:
        b=s.get(Batch,'a1');b.state='active';b.job_ids=[71,72]
    team,batch=service.spec(90,1,'a1');reader=gw or Reader()
    sid=comp.refresh(reader,team,batch)
    return comp,reader,sid


def test_rules_cas_freeze_and_invalid_numbers(service):
    comp=Competition(service.sessions);rules=comp.config()['rules']
    with pytest.raises(WorkflowError,match='finite'): comp.save_rules(90,0,{**rules,'good':float('nan')})
    comp.save_rules(90,0,rules)
    with pytest.raises(WorkflowError,match='changed'): comp.save_rules(90,0,rules)
    comp.save_rules(90,1,rules,True)
    with pytest.raises(WorkflowError,match='frozen'): comp.save_rules(90,2,rules)


def test_capture_is_idempotent_and_keeps_prior_payload(service):
    comp,reader,sid=capture(service)
    t,b=service.spec(90,1,'a1');assert comp.refresh(reader,t,b)==sid
    reader.payload['annotations']['101']['objects']=[]
    t,b=service.spec(90,1,'a1');new=comp.refresh(reader,t,b)
    assert new!=sid
    with service.sessions() as s:
        assert s.get(Submission,sid).payload['annotations']['101']['objects']
        assert s.get(Submission,new).version==2


def test_working_drafts_have_presence_without_becoming_submissions(service):
    comp=Competition(service.sessions);reader=Reader();reader.status='in_progress'
    with service.sessions.begin() as s: s.get(Batch,'a1').job_ids=[71]
    t,b=service.spec(90,1,'a1');assert comp.refresh(reader,t,b) is None
    page=comp.page(90,'a1');assert page['stats']['present']==2 and page['submission_id'] is None


def test_whole_unit_scores_once_and_na_pending_are_distinct(service):
    comp,reader,sid=capture(service,missing_penalty=True)
    comp.assess(90,'a1',sid,101,-1,'anything','whole','good')
    row=comp.leaderboard()[0];assert row['points']==1 and row['pending']==1 and row['provisional']
    comp.assess(90,'a1',sid,101,-1,'different','whole','bad','Wrong crown',revision=0)
    assert comp.leaderboard()[0]['points']==0
    with pytest.raises(WorkflowError,match='changed'): comp.assess(90,'a1',sid,101,-1,'unit','whole','good',revision=0)
    comp.assess(90,'a1',sid,102,-1,'unit','whole','na','No bird in this required image')
    assert comp.page(90,'a1')['stats']['pending']==0
    with service.sessions() as s: assert len(list(s.scalars(select(Assessment))))==2


def test_keypoint_partial_reviews_stay_in_pending_filter_and_missing_is_configurable(service):
    comp,reader,sid=capture(service,mode='keypoints',criteria=['box','beak','crown'],missing_penalty=True)
    comp.assess(90,'a1',sid,101,-1,'b1','box','good')
    comp.assess(90,'a1',sid,101,-1,'b1','beak','bad','Beak tip misplaced')
    page=comp.page(90,'a1',filter_name='pending');assert page['stats']['pending']==2 and page['filtered_total']==2
    comp.assess(90,'a1',sid,101,-1,'b1','crown','missing','Required visible crown omitted')
    row=comp.leaderboard()[0];assert row['points']==0 and row['good']==1 and row['missing']==1
    assert comp.page(90,'a1')['stats']['pending']==1


def test_new_submission_never_reuses_old_judgments(service):
    comp,reader,sid=capture(service);comp.assess(90,'a1',sid,101,-1,'unit','whole','good')
    reader.payload['annotations']['101']['tags']=[{'name':'changed','value':True}]
    t,b=service.spec(90,1,'a1');new=comp.refresh(reader,t,b)
    assert comp.leaderboard()[0]['points']==0
    with pytest.raises(WorkflowError,match='updated submission'):comp.assess(90,'a1',sid,102,-1,'unit','whole','good')
    assert comp.page(90,'a1',sid)['historical']
    assert comp.page(90,'a1',new)['stats']['pending']==2


def test_ownership_and_entity_bounds_are_enforced(service):
    comp,reader,sid=capture(service)
    with pytest.raises(WorkflowError,match='assigned'):comp.page(91,'a1')
    with pytest.raises(WorkflowError,match='outside'):comp.assess(90,'a1',sid,999,-1,'unit','whole','good')
    with pytest.raises(WorkflowError,match='Explain'):comp.assess(90,'a1',sid,101,-1,'unit','whole','missing')
    with pytest.raises(WorkflowError,match='omitted bird'):comp.assess(90,'a1',sid,101,-1,'invented','beak','good')
    assert comp.export(91)['submissions']==[]
    with service.sessions.begin() as s:s.get(Team,1).monitor_id=91
    with pytest.raises(WorkflowError,match='assigned'):comp.page(90,'a1',sid)


def test_video_frame_index_preserves_empty_and_not_annotated_frames():
    video={'objects':[{'key':'bird','classTitle':'bird','tags':[{'name':'bird_id','value':'b1'}]}],
           'frames':[{'index':0,'figures':[{'objectKey':'bird'}]}],
           'tags':[{'name':'empty','frameRange':[2,2]}]}
    units=list(annotation_units('video',201,video,frames=6,step=2))
    assert [u['frame_index'] for u in units]==[0,2,4]
    assert units[0]['present'] and units[1]['explicit_empty'] and not units[2]['present']
    assert not units[2]['explicit_empty']


def test_concurrent_capture_publishes_one_version(service):
    comp,reader,sid=capture(service)
    reader.payload['annotations']['101']['objects']=[]
    team,batch=service.spec(90,1,'a1')
    barrier=Barrier(2)
    def run():
        barrier.wait()
        try:return comp.refresh(reader,team,batch)
        except WorkflowError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert sum(r is not None for r in results)==1
    with service.sessions() as s:assert len(list(s.scalars(select(Submission))))==2


def test_common_challenge_excludes_nonchallenge_assets(service):
    comp,reader,sid=capture(service,common_source_ids=['bird1'])
    comp.assess(90,'a1',sid,101,-1,'unit','whole','good');comp.assess(90,'a1',sid,102,-1,'unit','whole','good')
    row=comp.leaderboard()[0];assert row['points']==1 and row['units']==1


def test_export_includes_reproducible_annotations_and_audit(service):
    comp,reader,sid=capture(service);comp.assess(90,'a1',sid,101,-1,'unit','whole','good')
    exported=comp.export(90)
    assert exported['schema_version']==2 and exported['configuration']['frozen']
    assert exported['submissions'][0]['payload']['annotations']['101']==labels()
    assert exported['assessments'][0]['verdict']=='good'
    assert any(a['action']=='assessment' for a in exported['audit'])


def test_monitor_scoped_poll_uses_one_lease_and_never_reads_foreign_teams(service):
    from monitoring.worker import poll_once
    class Gateway:
        def __init__(self):self.read=[]
        def progress(self,batch):self.read.append(batch.id);return {'status':'in_progress','completed':0,'total':1}
        def recent_activity(self,*args,**kwargs):return {}
    with service.sessions.begin() as s:
        for key in ('a1','b1'):
            b=s.get(Batch,key);b.state='active';b.job_ids=[1 if key=='a1' else 2]
    gateway=Gateway()
    assert poll_once(service,gateway,user_id=90)
    assert gateway.read==['a1']
    assert not poll_once(service,gateway,user_id=90)
    assert poll_once(service,gateway,user_id=91)
    assert gateway.read==['a1','b1']


def test_additive_schema_upgrade_keeps_legacy_work_and_reviews(service):
    from monitoring.db import Base, Review, initialize
    engine=service.sessions.kw['bind']
    with service.sessions.begin() as s:
        s.add(Review(batch_id='a1',entity_id=101,frame_index=-1,decision='accepted',note='Legacy evidence',monitor_id=90))
    added={'competition_config','registrations','submissions','work_units','assessments','simulation','uploaded_paths','source_origins','event_closure','task_attempts','task_progress'}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in added:table.drop(engine)
    initialize(engine)
    with service.sessions() as s:
        assert s.get(Team,1).name=='Team A'
        assert s.scalar(select(Review)).note=='Legacy evidence'
        assert s.get(CompetitionConfig,1).frozen is False
