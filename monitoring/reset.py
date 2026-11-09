"""Reviewed, backed-up resets of event state; native resources are retained."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import delete, select, update

from . import db
from .service import WorkflowError
from .setup import fingerprint


SCOPES = {
    'roster': 'Reset teams and monitors',
    'dataset': 'Reset dataset preparation',
    'checkin': 'Reset participant check-in',
    'event': 'Reset event rules',
    'monitoring': 'Restart event',
    'settings': 'Start setup again',
}
EFFECTS = {
    'roster': 'Clear imported teams, monitor assignments, arrivals, prepared jobs and results. Keep event rules and the team/workspace connection. Import your roster again.',
    'dataset': 'Clear staged upload records, distribution plans, prepared jobs and results. Keep teams, arrivals, event rules and existing uploaded datasets. Select a dataset and prepare jobs again.',
    'checkin': 'Mark every participant as not arrived. Keep teams, invitations, jobs and results.',
    'event': 'Restore default event rules and clear prepared jobs and results. Keep teams, arrivals, connection and uploaded datasets. Configure rules and prepare jobs again.',
    'monitoring': 'Clear prepared jobs, submissions, reviews, scores and event closure. Keep teams, arrivals, datasets and your rules. Unlock rules and clear the old deadline, then prepare jobs again.',
    'settings': 'Clear the team/workspace binding and all event setup, arrivals, jobs and results. Restore default rules. Keep your admin account signed in. Begin setup with the team and workspace IDs.',
}
MODELS = (
    db.Assessment, db.WorkUnit, db.Review, db.TaskProgress, db.TaskAttempt,
    db.Submission, db.Batch, db.PreparedJob, db.JobPlan, db.ArrivalCheckin,
    db.TeamOnboarding, db.Registration, db.Team, db.EventInvitee,
    db.SharedInvitation, db.EventMember, db.RosterContact, db.UploadedPath, db.Upload,
    db.MediaSequence, db.SourceOrigin, db.CompetitionConfig, db.EventClosure,
    db.EventConfig, db.EventPreferences,
)


class EventReset:
    def __init__(self, setup):
        self.setup, self.sessions = setup, setup.sessions

    def _owner(self, session, actor):
        if session.get(db.EventConfig, 1).owner_id != actor:
            raise WorkflowError('Only the event organiser can reset event data.')

    @staticmethod
    def snapshot(session):
        result = {}
        for model in MODELS:
            rows = [{c.name: deepcopy(getattr(row, c.name)) for c in model.__table__.columns
                     if c.name != 'local_token'} for row in session.scalars(select(model))]
            result[model.__tablename__] = sorted(rows, key=lambda r: str(r))
        return result

    def preview(self, actor, scope):
        if scope not in SCOPES:
            raise WorkflowError('Choose a valid tab to reset.')
        with self.sessions.begin() as session:
            self._owner(session, actor)
            if session.get(db.SetupState, 1).operation_id:
                raise WorkflowError('Finish or inspect the current operation before resetting.')
            if scope != 'checkin' and session.scalar(select(db.Batch.id).where(db.Batch.state == 'releasing').limit(1)):
                raise WorkflowError('Reconcile pending job releases before resetting.')
            # Do not lose jobs whose native result is still uncertain.
            if scope != 'checkin' and session.scalar(select(db.PreparedJob.id).where(db.PreparedJob.state != 'prepared').limit(1)):
                raise WorkflowError('Inspect active prepared jobs before resetting. Native activation is not supported by this reset.')
            snapshot = self.snapshot(session)
            jobs = []
            if scope != 'checkin':
                for batch in session.scalars(select(db.Batch)):
                    jobs.extend({'id': jid, 'team_id': batch.team_id, 'dataset_id': batch.dataset_id}
                                for jid in batch.job_ids)
            summary = {'scope': scope, 'title': SCOPES[scope], 'effect': EFFECTS[scope],
                       'native_jobs': len(jobs),
                       'backup': 'A backup of the current event records is saved before changes.',
                       'retained': 'Supervisely teams, memberships, invitations, uploaded files and annotations are retained. Current labeling jobs will be stopped.' if jobs else
                                   'Supervisely teams, memberships, invitations, uploaded files and annotations are retained.'}
            key = uuid4().hex
            session.add(db.SetupOperation(id=key, actor_id=actor, kind='reset', state='planned',
                details={'scope': scope, 'hash': fingerprint(snapshot), 'jobs': jobs, 'summary': summary}))
        return key, summary

    def apply(self, gateway, actor, plan_id):
        with self.sessions() as session:
            self._owner(session, actor)
            plan = session.get(db.SetupOperation, str(plan_id))
            if not plan or plan.kind != 'reset' or plan.state != 'planned' or plan.actor_id != actor:
                raise WorkflowError('Preview this reset before confirming it.')
            details = deepcopy(plan.details)
            if fingerprint(self.snapshot(session)) != details['hash']:
                raise WorkflowError('The event changed. Preview the reset again before confirming.')
        scope = details['scope']
        with self.setup.operation(actor, plan_id, 'reset', details, planned=True) as checkpoint:
            with self.sessions.begin() as session:
                self._owner(session, actor)
                snapshot = self.snapshot(session)
                if fingerprint(snapshot) != details['hash']:
                    raise WorkflowError('The event changed during reset. Inspect and preview again.')
                session.add(db.ResetBackup(id=plan_id, actor_id=actor, scope=scope, data=snapshot))
            # Validate every task before stopping any of them. A failed/uncertain
            # stop leaves all local records and the backup available for inspection.
            for job in details['jobs']:
                info = gateway.api.labeling_job.get_info_by_id(job['id'])
                if not info or info.team_id != job['team_id'] or info.dataset_id != job['dataset_id']:
                    raise WorkflowError('A native job changed ownership. Inspect it before resetting.')
            for job in details['jobs']:
                info = gateway.api.labeling_job.get_info_by_id(job['id'])
                if info.status not in ('completed', 'review_completed', 'stopped'):
                    gateway.api.labeling_job.stop(job['id'])
                    confirmed = gateway.api.labeling_job.get_info_by_id(job['id'])
                    if not confirmed or confirmed.status not in ('completed', 'review_completed', 'stopped'):
                        raise WorkflowError('Stopping a native job was not confirmed. Inspect it before resetting.')
                checkpoint({'stopped_job_id': job['id']})
            with self.sessions.begin() as session:
                if fingerprint(self.snapshot(session)) != details['hash']:
                    raise WorkflowError('The event changed while stopping jobs. Local records were kept; inspect before continuing.')
                if scope == 'checkin':
                    session.execute(delete(db.ArrivalCheckin))
                else:
                    for model in (db.Assessment, db.WorkUnit, db.Review, db.TaskProgress,
                                  db.TaskAttempt, db.Submission, db.Batch, db.PreparedJob,
                                  db.JobPlan, db.EventClosure):
                        session.execute(delete(model))
                    session.execute(update(db.Team).values(revision=db.Team.revision + 1))
                    rules = session.get(db.CompetitionConfig, 1)
                    rules.frozen = False
                    rules.rules = {} if scope in ('event', 'settings') else {**rules.rules, 'deadline': None}
                    rules.revision += 1
                    if scope in ('roster', 'settings'):
                        for model in (db.ArrivalCheckin, db.TeamOnboarding, db.Registration,
                                      db.Team, db.EventInvitee, db.RosterContact):
                            session.execute(delete(model))
                        session.execute(delete(db.EventMember).where(db.EventMember.user_id != actor))
                    if scope in ('dataset', 'settings'):
                        session.execute(delete(db.UploadedPath))
                        session.execute(delete(db.Upload))
                    if scope == 'settings':
                        session.execute(delete(db.MediaSequence))
                        session.execute(delete(db.SourceOrigin))
                        config = session.get(db.EventConfig, 1)
                        config.monitoring_team_id = config.annotator_team_id = config.source_workspace_id = None
                        prefs = session.get(db.EventPreferences, 1)
                        prefs.revision += 1
                        prefs.membership_synced_at = None
                    # Retain the invitation ledger so pending/unknown native
                    # invitations cannot accidentally be resent after reimport.
                    session.execute(update(db.SetupOperation).where(db.SetupOperation.state == 'planned',
                        db.SetupOperation.id != plan_id).values(state='invalidated'))
                session.add(db.Audit(team_id=0, batch_id='', actor_id=actor, action='event_reset',
                    details={'scope': scope, 'backup_id': plan_id}))
        return plan_id
