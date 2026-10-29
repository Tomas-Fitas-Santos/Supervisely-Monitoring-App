"""Versioned event rules, immutable captured submissions and reproducible judging.

Captured-at is deliberately distinct from a native submission timestamp: polling
cannot reconstruct a historical server state that was never downloaded.
"""
from copy import deepcopy
import hashlib
import json
import math
from uuid import uuid4

from sqlalchemy import select, update, func

from .db import Assessment, Audit, Batch, CompetitionConfig, Submission, Team, WorkUnit, now
from .service import WorkflowError

CRITERIA = ['whole', 'box', 'crown', 'left_eye', 'right_eye', 'beak', 'visibility', 'identity']
VERDICTS = ['good', 'bad', 'missing', 'na', 'pending']
DEFAULTS = {'name': 'Annotation event', 'mode': 'whole', 'good': 1, 'bad': 0, 'missing': -1,
            'missing_penalty': False, 'criteria': ['box', 'crown', 'left_eye', 'right_eye', 'beak'],
            'weights': {}, 'replicas': 1, 'seed': 2026, 'frame_step': 1,
            'release_gate': 'submission', 'deadline': None, 'common_source_ids': [], 'image_effort': 1,
            'batch_effort': 10, 'ranking': 'points', 'require_final_video': False, 'annotation_guide': 'Annotate each bird with its bounding box, crown, left_eye, right_eye, beak and visibility. Link objects with bird_id. The organisers must specify anatomical definitions, occluded-point and empty-frame rules before the event.'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def annotation_units(kind, entity_id, annotation, frames=None, step=1):
    """Only structural observations, never automatic anatomical quality judgments."""
    if kind == 'images':
        source = [(-1, annotation.get('objects', []))]
    else:
        objects = {o.get('key', o.get('id')): o for o in annotation.get('objects', [])}
        observed = {}
        for frame in annotation.get('frames', []):
            observed[frame['index']] = [{**objects.get(f.get('objectKey', f.get('objectId')), {}), **f}
                                        for f in frame.get('figures', [])]
        source = [(i, observed.get(i, [])) for i in range(0, frames, step)]
    for frame_index, labels in source:
        birds, issues = {}, []
        for label in labels:
            cls = label.get('classTitle', '')
            tags = {t.get('name'): t.get('value') for t in label.get('tags', [])}
            bird_id = str(tags.get('bird_id') or label.get('objectKey') or label.get('objectId') or '')
            if not bird_id:
                bird_id = 'unlinked'
                issues.append('Label lacks bird_id; verify association.')
            criterion = 'box' if cls == 'bird' else cls
            entries = birds.setdefault(bird_id, {})
            entries[criterion] = entries.get(criterion, 0) + 1
            if entries[criterion] > 1:
                issues.append(f'Duplicate {criterion} for bird {bird_id}.')
            if criterion in ('crown', 'left_eye', 'right_eye', 'beak') and tags.get('visibility') not in ('visible', 'occluded'):
                issues.append(f'{criterion}: visibility not specified.')
        missing = {bird: [c for c in ('box', 'crown', 'left_eye', 'right_eye', 'beak') if c not in present]
                   for bird, present in birds.items()}
        explicit_empty = any(t.get('name') == 'empty' and t.get('value') in (None, True, 'yes')
                             and (kind == 'images' or not t.get('frameRange') or t['frameRange'][0] <= frame_index <= t['frameRange'][1])
                             for t in annotation.get('tags', []))
        yield {'entity_id': entity_id, 'frame_index': frame_index, 'present': bool(labels),
               'birds': birds, 'missing': missing, 'explicit_empty': explicit_empty,
               'issues': sorted(set(issues)), 'structural': bool(birds) and not issues and not any(missing.values())}


def required_keys(unit, rules, entries):
    if rules['mode'] == 'whole':
        return [('unit', 'whole')]
    birds = set(unit['birds']) | {r['bird_id'] for r in entries if r['criterion'] != 'whole'}
    return [(bird, criterion) for bird in sorted(birds) for criterion in rules['criteria']] or [('unit', 'whole')]


class Competition:
    def __init__(self, sessions):
        self.sessions = sessions

    def config(self):
        with self.sessions() as s:
            row = s.get(CompetitionConfig, 1)
            return {'revision': row.revision, 'frozen': row.frozen, 'rules': {**deepcopy(DEFAULTS), **row.rules}}

    def save_rules(self, actor, revision, rules, freeze=False):
        if not isinstance(rules, dict) or set(rules) - set(DEFAULTS):
            raise WorkflowError('Unknown event rule fields.')
        result = {**deepcopy(DEFAULTS), **rules}
        if result['mode'] not in ('whole', 'keypoints', 'weighted') or result['ranking'] not in ('points', 'accuracy'):
            raise WorkflowError('Choose a supported scoring and ranking mode.')
        if result['release_gate'] not in ('submission', 'approval'):
            raise WorkflowError('Choose submission or approval as the release gate.')
        for field in ('replicas', 'frame_step', 'image_effort', 'batch_effort'):
            if type(result[field]) is not int or result[field] < 1:
                raise WorkflowError(f'{field} must be a positive integer.')
        if type(result['seed']) is not int or not isinstance(result['name'], str) or not result['name'].strip() or len(result['name']) > 180:
            raise WorkflowError('Enter an event name and integer allocation seed.')
        if not isinstance(result['annotation_guide'], str) or not result['annotation_guide'].strip() or len(result['annotation_guide']) > 16000:
            raise WorkflowError('Provide an annotation guide of at most 16000 characters.')
        if result['deadline'] is not None and (type(result['deadline']) is not int or result['deadline'] <= 0):
            raise WorkflowError('Deadline must be a positive UTC Unix timestamp or null.')
        if (not isinstance(result['criteria'], list) or not result['criteria'] or len(set(result['criteria'])) != len(result['criteria'])
                or set(result['criteria']) - set(CRITERIA[1:])):
            raise WorkflowError('Choose distinct supported scoring criteria.')
        if not isinstance(result['weights'], dict) or set(result['weights']) - set(CRITERIA):
            raise WorkflowError('Unknown criterion weight.')
        for value in [result['good'], result['bad'], result['missing'], *result['weights'].values()]:
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 100000:
                raise WorkflowError('Scores and weights must be finite numbers in range.')
        if any(v < 0 for v in result['weights'].values()) or result['good'] <= 0:
            raise WorkflowError('Use nonnegative weights and a positive good score.')
        if any(type(result[k]) is not bool for k in ('missing_penalty', 'require_final_video')):
            raise WorkflowError('Invalid rule toggle.')
        if not isinstance(result['common_source_ids'], list) or any(not isinstance(v, str) or len(v) > 200 for v in result['common_source_ids']):
            raise WorkflowError('Common challenge source IDs must be a list of strings.')
        with self.sessions.begin() as s:
            changed = s.execute(update(CompetitionConfig).where(CompetitionConfig.id == 1,
                CompetitionConfig.revision == int(revision), CompetitionConfig.frozen.is_(False))
                .values(rules=result, frozen=bool(freeze), revision=CompetitionConfig.revision + 1))
            if changed.rowcount != 1:
                raise WorkflowError('Rules were changed or frozen. Reload the event settings.')
            s.add(Audit(team_id=0, batch_id='', actor_id=actor, action='rules_frozen' if freeze else 'rules_saved',
                        details={'rules': result, 'revision': int(revision) + 1}))

    def _owned(self, s, uid, batch_id, organiser=False):
        batch = s.get(Batch, batch_id)
        team = s.get(Team, batch.team_id) if batch else None
        if not team or (not organiser and team.monitor_id != uid):
            raise WorkflowError('This team is not assigned to you.')
        return team, batch

    def latest(self, s, batch_id):
        return s.scalar(select(Submission).where(Submission.batch_id == batch_id).order_by(Submission.version.desc()).limit(1))

    def refresh(self, gateway, team, batch, reason='submission'):
        """Read only remotely; serialize publication with the existing team CAS."""
        if not batch.job_ids:
            return None
        before = gateway.progress(batch)
        states = before['status'].split(',')
        submitted = bool(states) and all(v in ('on_review', 'review_completed', 'completed') for v in states)
        if reason == 'submission' and not submitted:
            capture = False
        else:
            capture = True
        payload = gateway.capture_annotations(team, batch)
        after = gateway.progress(batch)
        if before['status'] != after['status']:
            raise WorkflowError('Job status changed during capture. Refresh again.')
        rules = self.config()['rules']
        units = [u for a in batch.assets for u in annotation_units(batch.kind, a['entity_id'],
                    payload['annotations'][str(a['entity_id'])], a.get('frames'), rules['frame_step'])]
        frame_step = rules['frame_step'] if batch.kind == 'video' else 1
        signature = digest({'payload': payload, 'job_ids': batch.job_ids, 'frame_step': frame_step})
        with self.sessions.begin() as s:
            # A handover, release or competing refresh cannot publish with an old owner/revision.
            changed = s.execute(update(Team).where(Team.id == team.id, Team.monitor_id == team.monitor_id,
                Team.revision == team.revision).values(revision=Team.revision + 1))
            if changed.rowcount != 1:
                raise WorkflowError('Team changed during annotation capture. Refresh again.')
            for u in units:
                row = s.scalar(select(WorkUnit).where(WorkUnit.batch_id == batch.id,
                    WorkUnit.entity_id == u['entity_id'], WorkUnit.frame_index == u['frame_index']))
                if row is None:
                    row = WorkUnit(batch_id=batch.id, entity_id=u['entity_id'], frame_index=u['frame_index'])
                    s.add(row)
                row.details, row.synced_at = u, now()
            previous = self.latest(s, batch.id)
            if not capture or (previous and previous.digest == signature):
                return previous.id if previous else None
            snap = Submission(id=uuid4().hex, batch_id=batch.id, version=(previous.version + 1 if previous else 1),
                              digest=signature, reason=reason, payload={**payload, 'units': units,
                              'job_ids': batch.job_ids, 'frame_step': frame_step, 'job_status': after['status'], 'rules_revision': self.config()['revision']})
            s.add(snap)
            s.add(Audit(team_id=team.id, batch_id=batch.id, actor_id=team.monitor_id,
                        action='submission_captured', details={'id': snap.id, 'digest': signature, 'reason': reason}))
            return snap.id

    def page(self, uid, batch_id, submission_id=None, offset=0, filter_name='all', organiser=False):
        offset = max(0, int(offset))
        with self.sessions() as s:
            self._owned(s, uid, batch_id, organiser=organiser)
            snap = s.get(Submission, submission_id) if submission_id else self.latest(s, batch_id)
            if snap and snap.batch_id != batch_id:
                raise WorkflowError('Submission does not belong to this batch.')
            versions = [{'id': row.id, 'version': row.version, 'captured_at': row.captured_at, 'reason': row.reason,
                         'digest': row.digest} for row in s.scalars(select(Submission).where(Submission.batch_id == batch_id).order_by(Submission.version.desc()))]
            rows = list(s.scalars(select(Assessment).where(Assessment.submission_id == snap.id))) if snap else []
            assessed = {}
            for r in rows:
                assessed.setdefault((r.entity_id, r.frame_index), []).append(self.assessment_json(r))
            units = snap.payload['units'] if snap else [r.details for r in s.scalars(select(WorkUnit).where(WorkUnit.batch_id == batch_id).order_by(WorkUnit.entity_id, WorkUnit.frame_index))]
            rules = self.config()['rules']
            def pending(u):
                entries = assessed.get((u['entity_id'], u['frame_index']), [])
                by_key = {(r['bird_id'], r['criterion']): r['verdict'] for r in entries}
                return any(by_key.get(k, 'pending') == 'pending' for k in required_keys(u, rules, entries))
            stats = {'total': len(units), 'present': sum(u['present'] for u in units),
                     'structural': sum(u['structural'] for u in units), 'empty': sum(u['explicit_empty'] for u in units),
                     'reviewed': sum(not pending(u) for u in units), 'pending': sum(pending(u) for u in units)}
            def matches(u):
                verdicts = [a['verdict'] for a in assessed.get((u['entity_id'], u['frame_index']), [])]
                return {'all': True, 'present': u['present'], 'incomplete': not u['structural'] and not u['explicit_empty'],
                        'empty': u['explicit_empty'], 'pending': pending(u),
                        'good': 'good' in verdicts, 'bad': 'bad' in verdicts, 'missing': 'missing' in verdicts}.get(filter_name, False)
            selected = [u for u in units if matches(u)]
            latest = self.latest(s, batch_id)
            return {'batch_id': batch_id, 'submission_id': snap.id if snap else None, 'version': snap.version if snap else None,
                    'current_version': latest.id if latest else None, 'historical': bool(snap and latest.id != snap.id),
                    'versions': versions, 'stats': stats, 'offset': offset, 'filtered_total': len(selected),
                    'units': [{**u, 'assessments': assessed.get((u['entity_id'], u['frame_index']), [])} for u in selected[offset:offset+40]]}

    @staticmethod
    def assessment_json(r):
        return {k: getattr(r, k) for k in ('id', 'entity_id', 'frame_index', 'bird_id', 'criterion', 'verdict', 'note', 'revision', 'monitor_id')}

    def assess(self, uid, batch_id, submission_id, entity, frame, bird_id, criterion, verdict, note='', revision=-1, adjudicate=False):
        config = self.config()
        if criterion == 'whole': bird_id = 'unit'
        if not config['frozen']:
            raise WorkflowError('Freeze event rules before recording competition judgments.')
        if criterion not in CRITERIA or verdict not in VERDICTS or not str(bird_id).strip() or len(str(bird_id)) > 100 or len(note) > 4000:
            raise WorkflowError('Choose a bird, supported criterion and verdict.')
        if verdict in ('bad', 'missing', 'na') and not note.strip():
            raise WorkflowError('Explain incorrect, missing or not-applicable judgments.')
        with self.sessions.begin() as s:
            from .db import SetupState
            lock = s.get(SetupState, 1)
            if lock and lock.operation_id:
                raise WorkflowError('Setup or handover is running or needs inspection.')
            team, batch = self._owned(s, uid, batch_id, organiser=adjudicate)
            snap = s.get(Submission, submission_id)
            if not snap or snap.batch_id != batch_id:
                raise WorkflowError('Capture and select a submitted version first.')
            if batch.kind == 'video' and snap.payload.get('frame_step', 1) != config['rules']['frame_step']:
                raise WorkflowError('Required frame rules changed after capture. Capture the submitted task again.')
            unit = next((u for u in snap.payload['units'] if u['entity_id'] == int(entity) and u['frame_index'] == int(frame)), None)
            if not unit:
                raise WorkflowError('Image/frame is outside the assessed submission.')
            if criterion != 'whole' and str(bird_id).strip() not in unit['birds'] and verdict not in ('missing', 'na', 'pending'):
                raise WorkflowError('For a wholly omitted bird, record missing criteria and explain the omission.')
            if self.latest(s, batch_id).id != snap.id:
                raise WorkflowError('An updated submission exists. Review its latest version.')
            changed = s.execute(update(Team).where(Team.id == team.id, Team.monitor_id == team.monitor_id, Team.revision == team.revision)
                                .values(revision=Team.revision + 1))
            if changed.rowcount != 1:
                raise WorkflowError('Team changed. Reload before judging.')
            row = s.scalar(select(Assessment).where(Assessment.submission_id == snap.id, Assessment.entity_id == int(entity),
                    Assessment.frame_index == int(frame), Assessment.bird_id == str(bird_id).strip(), Assessment.criterion == criterion))
            previous = self.assessment_json(row) if row else None
            if row:
                if row.revision != int(revision):
                    raise WorkflowError('Another judgment changed this criterion. Reload it.')
                row.revision += 1
            else:
                if int(revision) != -1:
                    raise WorkflowError('Judgment no longer exists. Reload it.')
                row = Assessment(submission_id=snap.id, entity_id=int(entity), frame_index=int(frame), bird_id=str(bird_id).strip(), criterion=criterion)
                s.add(row)
            row.verdict, row.note, row.monitor_id, row.updated_at = verdict, note.strip(), uid, now()
            s.add(Audit(team_id=team.id, batch_id=batch_id, actor_id=uid, action='adjudication' if adjudicate else 'assessment',
                        details={'submission_id': snap.id, 'previous': previous, 'entity_id': int(entity), 'frame_index': int(frame),
                                 'bird_id': str(bird_id).strip(), 'criterion': criterion, 'verdict': verdict, 'note': note.strip()}))

    def ensure_accepted(self, batch_id):
        rules = self.config()['rules']
        with self.sessions() as s:
            snap = self.latest(s, batch_id)
            if not snap:
                raise WorkflowError('Capture and judge the submitted task first.')
            rows = list(s.scalars(select(Assessment).where(Assessment.submission_id == snap.id)))
            for unit in snap.payload['units']:
                entries = [self.assessment_json(r) for r in rows if r.entity_id == unit['entity_id'] and r.frame_index == unit['frame_index']]
                indexed = {(r['bird_id'], r['criterion']): r['verdict'] for r in entries}
                if any(indexed.get(key) not in ('good', 'na') for key in required_keys(unit, rules, entries)):
                    raise WorkflowError('Native acceptance requires every required unit/criterion judged good or not applicable. Resolve corrections first.')
            return snap

    def leaderboard(self):
        rules = self.config()['rules']
        result = []
        with self.sessions() as s:
            from .db import EventClosure
            closed = s.get(EventClosure, 1) is not None
            for team in s.scalars(select(Team).order_by(Team.name)):
                points, good, bad, missing, pending, units_count = 0, 0, 0, 0, 0, 0
                for batch in s.scalars(select(Batch).where(Batch.team_id == team.id)):
                    snap = self.latest(s, batch.id)
                    if not snap:
                        continue
                    allowed_entities = {a['entity_id'] for a in batch.assets if not rules['common_source_ids'] or a['source_id'] in rules['common_source_ids']}
                    units = [u for u in snap.payload['units'] if u['entity_id'] in allowed_entities]
                    rows = list(s.scalars(select(Assessment).where(Assessment.submission_id == snap.id)))
                    units_count += len(units)
                    for unit in units:
                        entries = [r for r in rows if r.entity_id == unit['entity_id'] and r.frame_index == unit['frame_index']]
                        expected = required_keys(unit, rules, [self.assessment_json(r) for r in entries])
                        by_key = {(r.bird_id, r.criterion): r for r in entries}
                        for key in expected:
                            row = by_key.get(key)
                            verdict = row.verdict if row else 'pending'
                            if verdict == 'pending': pending += 1; continue
                            if verdict == 'na': continue
                            good += verdict == 'good'; bad += verdict == 'bad'; missing += verdict == 'missing'
                            value = rules[verdict] if verdict != 'missing' or rules['missing_penalty'] else 0
                            weight = rules['weights'].get(key[1], 1) if rules['mode'] == 'weighted' else 1
                            points += value * weight
                assessed = good + bad + missing
                result.append({'team_id': team.id, 'team': team.name, 'monitor_id': team.monitor_id,
                    'points': points, 'good': good, 'bad': bad, 'missing': missing, 'pending': pending,
                    'units': units_count, 'accuracy': round(good / assessed, 4) if assessed else None,
                    'provisional': not closed or pending > 0 or not units_count})
        result.sort(key=lambda row: (-(row['points'] if rules['ranking'] == 'points' else (row['accuracy'] or 0)), row['team']))
        last_score, position = None, 0
        for i, row in enumerate(result, 1):
            score = row['points'] if rules['ranking'] == 'points' else row['accuracy']
            if i == 1 or score != last_score: position = i
            row['position'] = position
            last_score = score
        return result

    def export(self, uid, organiser=False):
        with self.sessions() as s:
            teams = list(s.scalars(select(Team) if organiser else select(Team).where(Team.monitor_id == uid)))
            ids = {t.id for t in teams}
            batches = list(s.scalars(select(Batch).where(Batch.team_id.in_(ids))))
            batch_ids = {b.id for b in batches}
            snapshots = list(s.scalars(select(Submission).where(Submission.batch_id.in_(batch_ids))))
            submission_ids = {r.id for r in snapshots}
            def serialize(rows): return [{c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows]
            from .db import Review, SourceOrigin, TaskAttempt, EventClosure, Registration
            closure = s.get(EventClosure, 1)
            return {'schema_version': 2, 'registrations': serialize(s.scalars(select(Registration))) if organiser else [], 'closure': serialize([closure]) if closure else [],
                    'source_origins': serialize(s.scalars(select(SourceOrigin))),
                    'task_attempts': serialize(s.scalars(select(TaskAttempt).where(TaskAttempt.batch_id.in_(batch_ids)))),
                    'operational_reviews': serialize(s.scalars(select(Review).where(Review.batch_id.in_(batch_ids)))), 'exported_at': now(), 'capture_notice': 'Captured when observed; not guaranteed exact native submission-time snapshots.',
                    'configuration': self.config(), 'teams': serialize(teams), 'batches': serialize(batches),
                    'submissions': serialize(snapshots), 'assessments': serialize(s.scalars(select(Assessment).where(Assessment.submission_id.in_(submission_ids)))),
                    'audit': serialize(s.scalars(select(Audit).where(Audit.team_id.in_(ids | ({0} if organiser else set())))))}
