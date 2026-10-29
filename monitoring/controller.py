from .service import WorkflowError


def release(service, gateway, user_id, team_id, batch_id, revision):
    team, batch = service.spec(user_id, team_id, batch_id)
    gateway.validate(team, batch)  # Read-only preflight before reserving a remote mutation.
    batch.release_key = f'nightjar:{batch.id}'
    if gateway.find_release(team, batch):
        raise WorkflowError('Existing remote jobs found; reconcile this release before creating anything.')
    service.reserve_release(user_id, team_id, batch_id, revision)
    team, batch = service.spec(user_id, team_id, batch_id)
    try:
        ids = gateway.create_release(team, batch)
        service.finish_release(user_id, team_id, batch_id, ids)
    except Exception:
        service.release_failed(user_id, team_id, batch_id)
        raise WorkflowError('Release result is uncertain. Use Reconcile; do not create the jobs again.') from None


def reconcile(service, gateway, user_id, team_id, batch_id):
    team, batch = service.spec(user_id, team_id, batch_id)
    if batch.state != 'releasing':
        raise WorkflowError('This batch has no pending release.')
    ids = gateway.find_release(team, batch)
    if len(ids) != len(gateway.slices(team, batch)):
        raise WorkflowError('Not all jobs were found. An organiser must investigate the remote result; no automatic retry.')
    service.finish_release(user_id, team_id, batch_id, ids)


def sync(service, gateway, team, batch, activity=False):
    if not batch.job_ids:
        return
    try:
        service.update_progress(batch.id, gateway.progress(batch))
    except Exception:
        service.sync_failed(batch.id)
        return
    if activity:
        try:
            service.update_activity(batch.id, gateway.recent_activity(team, batch))
        except Exception:
            # Activity timestamps stay old; never fabricate activity or invalidate valid progress.
            pass


def approve(service, setup, gateway, uid, team_id, batch_id, revision, note):
    """Complete native review only for a fully judged, unchanged captured version."""
    from uuid import uuid4
    from .competition import Competition, digest
    from .db import Team, Batch, now
    from sqlalchemy import update
    competition=Competition(service.sessions)
    team,batch=service.spec(uid,team_id,batch_id)
    snapshot=competition.ensure_accepted(batch_id)
    if not note.strip() or len(note)>4000:
        raise WorkflowError('Record the inspection summary before native acceptance.')
    statuses=gateway.progress(batch)['status'].split(',')
    if any(v not in ('on_review','completed','review_completed') for v in statuses):
        raise WorkflowError('All participant jobs must be submitted before acceptance.')
    payload=gateway.capture_annotations(team,batch)
    if digest({'payload':payload,'job_ids':batch.job_ids,'frame_step':competition.config()['rules']['frame_step'] if batch.kind=='video' else 1})!=snapshot.digest:
        raise WorkflowError('Annotations changed after the judged capture. Capture and review the new version.')
    key=uuid4().hex
    with setup.operation(uid,key,'native_acceptance',{'batch_id':batch_id,'submission_id':snapshot.id}) as checkpoint:
        with service.sessions.begin() as s:
            changed=s.execute(update(Team).where(Team.id==team_id,Team.monitor_id==uid,Team.revision==int(revision)).values(revision=Team.revision+1))
            if changed.rowcount!=1:raise WorkflowError('Team changed before native acceptance.')
        for jid in batch.job_ids:
            job=gateway.api.labeling_job.get_info_by_id(jid)
            if not job or job.team_id!=team_id or job.dataset_id!=batch.dataset_id:
                raise WorkflowError('Native task identity changed. Inspect the acceptance operation.')
            for entity in job.entities:
                entity_id=entity['id'] if isinstance(entity,dict) else entity
                gateway.api.labeling_job.set_entity_review_status(jid,entity_id,'accepted')
            gateway.api.labeling_job.set_status(jid,'completed')
            confirmed=gateway.api.labeling_job.get_info_by_id(jid)
            if confirmed.status not in ('completed','review_completed'):
                raise WorkflowError('Native completion was not confirmed. Inspect the acceptance operation.')
            checkpoint({'completed_job_id':jid})
        service.update_progress(batch_id,gateway.progress(batch))
        service.approve(uid,team_id,batch_id,int(revision)+1,note,operation_id=key)
