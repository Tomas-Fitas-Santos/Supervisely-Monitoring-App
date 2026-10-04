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
