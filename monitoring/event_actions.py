"""Journaled native correction tasks, accepted review completion and event closure."""
from uuid import uuid4
from sqlalchemy import select, update
from .competition import Competition
from .db import Assessment, Audit, Batch, EventClosure, Submission, TaskAttempt, Team, now
from .service import WorkflowError


def corrections(service, setup, gateway, uid, batch_id, revision):
    competition = Competition(service.sessions)
    with service.sessions() as s:
        team, batch = competition._owned(s, uid, batch_id)
        if team.revision != int(revision): raise WorkflowError('Team changed. Reload before returning corrections.')
        snap = competition.latest(s, batch_id)
        if not snap: raise WorkflowError('Capture the submitted task before requesting corrections.')
        issues = list(s.scalars(select(Assessment).where(Assessment.submission_id == snap.id,
                       Assessment.verdict.in_(['bad','missing']))))
        if not issues: raise WorkflowError('Record an incorrect or missing criterion first.')
        if s.get(EventClosure, 1): raise WorkflowError('This event is closed. New correction tasks are disabled.')
    states = gateway.progress(batch)['status'].split(',')
    if any(v not in ('on_review','completed','review_completed') for v in states):
        raise WorkflowError('Wait until all current jobs have been submitted.')
    report = 'Corrections requested by the event monitor. Existing annotations are retained.\n' + '\n'.join(
        f'Entity {r.entity_id}, frame {r.frame_index if r.frame_index >= 0 else "image"}, bird {r.bird_id}, {r.criterion}: {r.verdict}. {r.note}' for r in issues)
    affected = {r.entity_id for r in issues}
    key = uuid4().hex
    with setup.operation(uid, key, 'correction_tasks', {'batch_id':batch.id,'submission_id':snap.id,'report':report}) as checkpoint:
        with service.sessions.begin() as s:
            changed=s.execute(update(Team).where(Team.id==team.id,Team.monitor_id==uid,Team.revision==int(revision)).values(revision=Team.revision+1))
            if changed.rowcount != 1: raise WorkflowError('Team changed while requesting corrections.')
        jobs=[]
        for jid in batch.job_ids:
            job=gateway.api.labeling_job.get_info_by_id(jid)
            if not job or job.team_id!=team.id or job.dataset_id!=batch.dataset_id:
                raise WorkflowError('Native task ownership changed. Inspect the correction operation.')
            entities=[e['id'] if isinstance(e,dict) else e for e in job.entities]
            if not affected.intersection(entities): jobs.append(jid);continue
            # Reissue this member's whole task, preserving the disjoint ownership and denominator.
            # Stopping first prevents concurrent editing through the superseded native task.
            gateway.api.labeling_job.stop(jid)
            checkpoint({'stopped_job_id':jid})
            created=gateway.api.labeling_job.create(name=f'correction:{key}:u{job.assigned_to_id}',dataset_id=batch.dataset_id,
                user_ids=[job.assigned_to_id],images_ids=entities,reviewer_id=team.monitor_id,
                description=report[:16000],readme=report[:16000],enable_quality_check=True,dynamic_classes=False,dynamic_tags=False)
            if len(created)!=1: raise WorkflowError('Unexpected native correction result. Inspect the operation.')
            jobs.append(created[0].id);checkpoint({'new_job_id':created[0].id,'parent_job_id':jid})
        with service.sessions.begin() as s:
            current=s.get(Batch,batch.id);current.job_ids=jobs;current.state='active';current.remote_status='pending';current.synced_at=None
            s.add(TaskAttempt(id=key,batch_id=batch.id,parent_job_ids=batch.job_ids,job_ids=jobs,report=report))
            s.add(Audit(team_id=team.id,batch_id=batch.id,actor_id=uid,action='corrections_returned',details={'attempt_id':key,'job_ids':jobs}))


def close_event(service, setup, gateway, actor):
    competition=Competition(service.sessions)
    config=competition.config()
    if not config['frozen']: raise WorkflowError('Freeze event rules before closing the event.')
    with service.sessions() as s:
        if s.get(EventClosure,1): return
        work=[(s.get(Team,b.team_id),b) for b in s.scalars(select(Batch).where(Batch.state.in_(['active','approved']))) if b.job_ids]
    key=uuid4().hex
    with setup.operation(actor,key,'event_closure',{'deadline':config['rules']['deadline']}) as checkpoint:
        # Stop every current task before reading final annotation payloads.
        for team,batch in work:
            for jid in batch.job_ids:
                info = gateway.api.labeling_job.get_info_by_id(jid)
                if not info or info.team_id != team.id or info.dataset_id != batch.dataset_id:
                    raise WorkflowError('Native task identity changed before closure. Inspect the operation.')
                if info.status != 'completed': gateway.api.labeling_job.stop(jid)
                checkpoint({'stopped_job_id':jid})
        for team,batch in work:
            with service.sessions() as s: current=s.get(Team,team.id)
            sid=competition.refresh(gateway,current,batch,reason='deadline')
            checkpoint({'batch_id':batch.id,'submission_id':sid})
        with service.sessions.begin() as s:
            s.add(EventClosure(id=1,closed_at=now(),deadline=config['rules']['deadline'],operation_id=key))
            s.add(Audit(team_id=0,batch_id='',actor_id=actor,action='event_closed',details={'operation_id':key,'observed_at':now(),'deadline':config['rules']['deadline']}))


def video_handover(service, setup, gateway, uid, batch_id, revision, annotator_id):
    competition=Competition(service.sessions)
    with service.sessions() as s:
        team,batch=competition._owned(s,uid,batch_id)
        if s.get(EventClosure,1): raise WorkflowError('The event is closed.')
    annotator_id=int(annotator_id)
    if batch.kind!='video' or len(batch.job_ids)!=1 or annotator_id not in team.annotator_ids:
        raise WorkflowError('Choose a released whole-video task and one of this pair’s participants.')
    key=uuid4().hex
    with setup.operation(uid,key,'video_handover',{'batch_id':batch_id,'annotator_id':annotator_id}) as checkpoint:
        with service.sessions.begin() as s:
            changed=s.execute(update(Team).where(Team.id==team.id,Team.monitor_id==uid,Team.revision==int(revision)).values(revision=Team.revision+1))
            if changed.rowcount!=1:raise WorkflowError('Team changed before video handover.')
        job=gateway.api.labeling_job.get_info_by_id(batch.job_ids[0])
        if not job or job.team_id!=team.id or job.dataset_id!=batch.dataset_id:
            raise WorkflowError('Native video task identity changed. Inspect the handover.')
        gateway.api.labeling_job.set_assignees(job.id,annotator_id=annotator_id)
        confirmed=gateway.api.labeling_job.get_info_by_id(job.id)
        if confirmed.assigned_to_id!=annotator_id:raise WorkflowError('Video handover was not confirmed.')
        checkpoint({'job_id':job.id,'previous_annotator_id':job.assigned_to_id,'annotator_id':annotator_id})
        with service.sessions.begin() as s:
            current=s.get(Batch,batch_id)
            current.assets=[dict(a,assignee_id=annotator_id) for a in current.assets]
            s.add(Audit(team_id=team.id,batch_id=batch_id,actor_id=uid,action='video_handover',
                        details={'previous_annotator_id':job.assigned_to_id,'annotator_id':annotator_id,'job_id':job.id}))
