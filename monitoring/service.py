from sqlalchemy import select, update

from .db import Audit, Batch, Review, SetupState, Team, now


class WorkflowError(ValueError):
    """An expected access or workflow rejection safe to show in the UI."""


class Service:
    def __init__(self, sessions, stale_after=180):
        self.sessions = sessions
        self.stale_after = stale_after

    def _team(self, session, user_id, team_id, revision=None):
        team = session.get(Team, team_id)
        if team is None or team.monitor_id != user_id:
            raise WorkflowError("This team is not assigned to you.")
        if revision is not None:
            changed = session.execute(update(Team).where(
                Team.id == team_id, Team.revision == revision
            ).values(revision=Team.revision + 1))
            if changed.rowcount != 1:
                raise WorkflowError("Another action changed this team. Refresh and try again.")
        return team

    @staticmethod
    def _batch(session, team_id, batch_id):
        batch = session.get(Batch, batch_id)
        if batch is None or batch.team_id != team_id:
            raise WorkflowError("Batch does not belong to this team.")
        return batch

    @staticmethod
    def _audit(session, team_id, batch_id, user_id, action, details):
        session.add(Audit(team_id=team_id, batch_id=batch_id, actor_id=user_id,
                          action=action, details=details))

    def snapshot(self, user_id):
        with self.sessions() as s:
            teams = list(s.scalars(select(Team).where(Team.monitor_id == user_id).order_by(Team.name)))
            result = []
            for team in teams:
                batches = list(s.scalars(select(Batch).where(Batch.team_id == team.id).order_by(Batch.position)))
                entries = []
                for b in batches:
                    reviews = list(s.scalars(select(Review).where(Review.batch_id == b.id).order_by(Review.updated_at.desc())))
                    entries.append({
                        'id': b.id, 'position': b.position, 'kind': b.kind, 'state': b.state,
                        'job_ids': b.job_ids, 'remote_status': b.remote_status,
                        'completed': b.completed, 'total': b.total, 'assets': b.assets,
                        'synced_at': b.synced_at, 'sync_error': b.sync_error,
                        'stale': b.synced_at is None or now() - b.synced_at > self.stale_after,
                        'activity': b.activity, 'activity_at': b.activity_at,
                        'reviews': [{'entity_id': r.entity_id, 'frame_index': r.frame_index,
                                     'decision': r.decision, 'note': r.note, 'monitor_id': r.monitor_id,
                                     'updated_at': r.updated_at} for r in reviews],
                        'open_flags': sum(r.decision == 'correction' for r in reviews),
                    })
                result.append({'id': team.id, 'name': team.name, 'revision': team.revision,
                               'annotator_ids': team.annotator_ids, 'batches': entries})
            return {'teams': result, 'refreshed_at': now(), 'user_id': user_id}

    def spec(self, user_id, team_id, batch_id):
        with self.sessions() as s:
            team = self._team(s, user_id, team_id)
            batch = self._batch(s, team_id, batch_id)
            return team, batch

    def review(self, user_id, team_id, batch_id, revision, entity_id, frame_index, decision, note):
        if decision not in ('accepted', 'correction') or not note.strip() or len(note) > 4000:
            raise WorkflowError("Choose a decision and provide a note (up to 4000 characters).")
        with self.sessions.begin() as s:
            self._team(s, user_id, team_id, revision)
            b = self._batch(s, team_id, batch_id)
            if b.state in ('locked', 'releasing'):
                raise WorkflowError("Release the batch before recording a review.")
            asset = next((a for a in b.assets if a['entity_id'] == entity_id), None)
            if asset is None:
                raise WorkflowError("The entity is not in this batch.")
            if (b.kind == 'images' and frame_index != -1) or (
                b.kind == 'video' and not 0 <= frame_index < asset['frames']
            ):
                raise WorkflowError("Invalid frame index; video frames are numbered from zero.")
            r = s.scalar(select(Review).where(Review.batch_id == batch_id,
                Review.entity_id == entity_id, Review.frame_index == frame_index))
            if r is None:
                r = Review(batch_id=batch_id, entity_id=entity_id, frame_index=frame_index)
                s.add(r)
            r.decision, r.note, r.monitor_id, r.updated_at = decision, note.strip(), user_id, now()
            if decision == 'correction' and b.state == 'approved':
                b.state = 'active'  # Past work can be reopened; future release is blocked.
            self._audit(s, team_id, batch_id, user_id, 'review', {
                'entity_id': entity_id, 'frame_index': frame_index, 'decision': decision, 'note': note.strip()})

    def approve(self, user_id, team_id, batch_id, revision, note):
        if not note.strip() or len(note) > 4000:
            raise WorkflowError("Record what you inspected before approving the batch.")
        with self.sessions.begin() as s:
            self._team(s, user_id, team_id, revision)
            b = self._batch(s, team_id, batch_id)
            if b.state != 'active' or b.sync_error or not b.synced_at or now() - b.synced_at > self.stale_after:
                raise WorkflowError("Approval requires an active batch with fresh successful progress data.")
            statuses = b.remote_status.split(',')
            if not statuses or any(v not in ('on_review', 'completed', 'review_completed') for v in statuses):
                raise WorkflowError("All participant jobs must be submitted for review first.")
            if b.kind == 'images' and (b.total != len(b.assets) or b.completed != b.total):
                raise WorkflowError("Not all assigned images are complete.")
            reviews = list(s.scalars(select(Review).where(Review.batch_id == batch_id)))
            if not reviews or any(r.decision == 'correction' for r in reviews):
                raise WorkflowError("Inspect a sample and resolve all correction flags first.")
            b.state = 'approved'
            self._audit(s, team_id, batch_id, user_id, 'approve', {'note': note.strip()})

    def reserve_release(self, user_id, team_id, batch_id, revision):
        with self.sessions.begin() as s:
            setup = s.get(SetupState, 1)
            if setup and setup.operation_id:
                raise WorkflowError('Event setup is running or needs inspection. Release after setup is complete.')
            self._team(s, user_id, team_id, revision)
            b = self._batch(s, team_id, batch_id)
            batches = list(s.scalars(select(Batch).where(Batch.team_id == team_id).order_by(Batch.position)))
            next_batch = next((v for v in batches if v.state == 'locked'), None)
            if next_batch is None or next_batch.id != batch_id:
                raise WorkflowError("Release the next locked batch in sequence.")
            if any(v.state != 'approved' for v in batches if v.position < b.position):
                raise WorkflowError("Approve all earlier batches and resolve their corrections first.")
            b.state, b.release_key = 'releasing', f'nightjar:{b.id}'
            self._audit(s, team_id, batch_id, user_id, 'release_reserved', {'key': b.release_key})

    def finish_release(self, user_id, team_id, batch_id, job_ids):
        if not job_ids or len(set(job_ids)) != len(job_ids):
            raise WorkflowError("The remote job result is incomplete.")
        with self.sessions.begin() as s:
            self._team(s, user_id, team_id)
            s.execute(update(Team).where(Team.id == team_id).values(revision=Team.revision + 1))
            changed = s.execute(update(Batch).where(Batch.id == batch_id, Batch.team_id == team_id,
                Batch.state == 'releasing').values(job_ids=job_ids, state='active', remote_status='pending', sync_error=None))
            if changed.rowcount != 1:
                raise WorkflowError("This batch has no pending release.")
            self._audit(s, team_id, batch_id, user_id, 'release_confirmed', {'job_ids': job_ids})

    def release_failed(self, user_id, team_id, batch_id):
        with self.sessions.begin() as s:
            self._team(s, user_id, team_id)
            self._batch(s, team_id, batch_id).sync_error = 'Release outcome unknown; reconcile before continuing.'
            self._audit(s, team_id, batch_id, user_id, 'release_uncertain', {})

    def update_progress(self, batch_id, data):
        with self.sessions.begin() as s:
            b = s.get(Batch, batch_id)
            b.remote_status = data['status']
            b.completed, b.total = data['completed'], data['total']
            b.synced_at, b.sync_error = now(), None

    def sync_failed(self, batch_id):
        with self.sessions.begin() as s:
            s.get(Batch, batch_id).sync_error = 'Supervisely sync failed. Last successful values retained.'

    def update_activity(self, batch_id, data):
        with self.sessions.begin() as s:
            b = s.get(Batch, batch_id)
            b.activity, b.activity_at = data, now()
