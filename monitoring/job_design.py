"""Deterministic whole-video and overlapping-image allocation into logical jobs.

A logical job can contain multiple native labeling-job slices because
Supervisely projects have a single media type. The *logical* job is always
reviewed and advanced as a single competition-team unit.
"""
from collections import Counter, defaultdict
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from .db import Audit, EventConfig, EventMember, JobPlan, PreparedJob, Registration, SharedInvitation, now
from .event_settings import single_team
from .service import WorkflowError


def _tie(seed, text):
    return hashlib.sha256(f'{seed}:{text}'.encode()).hexdigest()


def _integer(config, key, lo, hi):
    value = config.get(key)
    if isinstance(value, bool):
        raise WorkflowError(f'{key} must be an integer from {lo} to {hi}.')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise WorkflowError(f'{key} must be an integer from {lo} to {hi}.') from None
    if str(number) != str(value).strip() or not lo <= number <= hi:
        raise WorkflowError(f'{key} must be an integer from {lo} to {hi}.')
    return number


def plan_jobs(teams, assets, config):
    """Return job assignments without native API calls or database mutation.

    Required: jobs_per_team, videos_per_team, image_replicas,
    video_replicas, image_common_percent, seed.
    """
    if not isinstance(config, dict) or not isinstance(assets, list):
        raise WorkflowError('Choose valid job settings and source datasets.')
    if not teams or len(set(teams)) != len(teams):
        raise WorkflowError('Choose distinct registered competition teams.')
    teams = sorted(teams)
    n = len(teams)
    jobs = _integer(config, 'jobs_per_team', 1, 100)
    videos_per_team = _integer(config, 'videos_per_team', 0, 100)
    image_replicas = _integer(config, 'image_replicas', 1, n)
    video_replicas = _integer(config, 'video_replicas', 1, n)
    common_percent = _integer(config, 'image_common_percent', 0, 100)
    seed = _integer(config, 'seed', 0, 2147483647)
    images = [a for a in assets if a.get('kind') == 'images']
    videos = [a for a in assets if a.get('kind') == 'video']
    ids = [a.get('source_id') for a in assets]
    if (not assets or len(set(ids)) != len(ids) or any(
            not a.get('source_id') or a.get('kind') not in ('images','video')
            or type(a.get('units')) is not int or a['units'] <= 0
            or not a.get('dataset_id') for a in assets)):
        raise WorkflowError('Upload a valid image/video source inventory first.')
    if videos_per_team > len(videos):
        raise WorkflowError('Not enough complete videos to give each team the requested distinct count.')
    if videos_per_team*n > video_replicas*len(videos):
        raise WorkflowError('Video overlap is too low for the requested number of videos per team.')
    if not images and common_percent:
        raise WorkflowError('Image overlap requested, but no images were uploaded.')

    assigned = {team: [] for team in teams}
    loads = dict.fromkeys(teams, 0)
    category_loads = {t: Counter() for t in teams}
    cover = Counter()
    video_sets = {t: set() for t in teams}
    video_assets = sorted(videos, key=lambda a: (-a['units'],
                          _tie(seed, a['source_id'])))
    # Assign whole videos in rounds, balancing both source frequency and effort.
    for _ in range(videos_per_team):
        for team in sorted(teams, key=lambda t: (loads[t], _tie(seed, f'vteam:{t}'))):
            candidates = [a for a in video_assets
                          if a['source_id'] not in video_sets[team]
                          and cover[a['source_id']] < video_replicas]
            if not candidates:
                raise WorkflowError('Cannot allocate the selected videos under the overlap limit.')
            chosen = min(candidates, key=lambda a: (
                cover[a['source_id']], loads[team] + a['units'],
                category_loads[team][str(a.get('stratum') or 'videos')],
                _tie(seed, f'video:{team}:{a["source_id"]}')))
            assigned[team].append(chosen)
            loads[team] += chosen['units']
            video_sets[team].add(chosen['source_id'])
            cover[chosen['source_id']] += 1
            category_loads[team][str(chosen.get('stratum') or 'videos')] += 1

    sorted_images = sorted(images, key=lambda a: (
        _tie(seed, a['source_id']), a['source_id']))
    common_count = len(sorted_images)*common_percent//100
    common_ids = {a['source_id'] for a in sorted_images[:common_count]}
    for image in sorted(sorted_images, key=lambda a: (
            -a['units'], _tie(seed, a['source_id']))):
        k = n if image['source_id'] in common_ids else image_replicas
        stratum = str(image.get('stratum') or 'images')
        choices = sorted(teams, key=lambda t: (
            loads[t], category_loads[t][stratum],
            _tie(seed, f'image:{image["source_id"]}:{t}')))[:k]
        for team in choices:
            assigned[team].append(image)
            loads[team] += image['units']
            category_loads[team][stratum] += 1

    job_assignments = {}
    rows = []
    coverage = Counter()
    for team in teams:
        inventory = sorted(assigned[team], key=lambda a: (
            -a['units'], _tie(seed, f'unit:{team}:{a["source_id"]}')))
        if len(inventory) < jobs:
            raise WorkflowError(
                f'Team {team} only has {len(inventory)} whole media units but '
                f'{jobs} labeling jobs were requested. Add data or reduce jobs.'
            )
        # Seed every job with one item to guarantee no empty jobs.
        packets = [[] for _ in range(jobs)]
        effort = [0]*jobs
        for index, item in enumerate(inventory):
            pos = index if index < jobs else min(
                range(jobs), key=lambda j: (effort[j], len(packets[j]), j))
            packets[pos].append(dict(item))
            effort[pos] += item['units']
            coverage[item['source_id']] += 1
        job_assignments[team] = packets
        rows.append({
            'registration_id': team,
            'images': sum(a['kind'] == 'images' for a in inventory),
            'videos': sum(a['kind'] == 'video' for a in inventory),
            'jobs': len(packets),
            'total_effort': sum(effort),
            'job_efforts': effort,
            'job_contents': [
                {'position': i+1, 'images': sum(a['kind']=='images' for a in group),
                 'videos':sum(a['kind']=='video' for a in group),
                 'effort': effort[i], 'sources':[a['source_id'] for a in group]}
                for i, group in enumerate(packets)
            ],
        })
    preview = {
        'teams': rows, 'team_count': n, 'jobs_per_team': jobs,
        'total_jobs': n*jobs, 'image_overlap_teams': image_replicas,
        'video_overlap_teams': video_replicas,
        'common_images': common_count,
        'source_images': len(images), 'source_videos': len(videos),
        'workload_spread': max(loads.values())-min(loads.values()),
        'coverage': dict(coverage), 'config': dict(config),
    }
    return job_assignments, preview


class PreparedJobs:
    def __init__(self, sessions):
        self.sessions = sessions

    def _registered(self, session, team_ids):
        if not team_ids or len(team_ids) != len(set(team_ids)):
            raise WorkflowError('Select competition pairs before configuring jobs.')
        result = []
        for reg_id in team_ids:
            reg = session.get(Registration, str(reg_id))
            if not reg or len(reg.members or []) != 2 or not reg.monitor_id:
                raise WorkflowError(
                    'Each selected competition pair needs two members and an assigned monitor.'
                )
            event = session.get(EventConfig, 1)
            if not event or int(reg.monitor_id) != int(event.owner_id or 0):
                staff = session.get(EventMember, int(reg.monitor_id))
                if not staff or staff.group != 'monitors':
                    raise WorkflowError(
                        'The assigned monitor must have accepted the CSV-declared '
                        'staff role before labeling jobs can be prepared.'
                    )
            result.append(reg)
        return result

    def preview(self, actor, team_ids, assets, dataset_ids, config, fingerprint):
        with self.sessions() as session:
            event = session.get(EventConfig, 1)
            if not single_team(event):
                raise WorkflowError('Configure the one existing Supervisely team first.')
            if event.owner_id != actor:
                raise WorkflowError('Only the event organiser can prepare jobs.')
            regs = self._registered(session, team_ids)
            if session.scalar(select(PreparedJob.id).limit(1)):
                raise WorkflowError('Jobs have already been prepared. Do not overwrite event work.')
            invited = {v.email: v for v in session.scalars(
                select(SharedInvitation).where(
                    SharedInvitation.team_id == event.monitoring_team_id))}
            for reg in regs:
                if any((p.get('email') or '').casefold() not in invited or
                       invited[(p.get('email') or '').casefold()].status != 'accepted'
                       for p in reg.members):
                    raise WorkflowError(
                        f'Both participants must accept email invitations before jobs '
                        f'can be prepared: {reg.team_name}'
                    )
        assignments, preview = plan_jobs(team_ids, assets, config)
        plan_id = uuid4().hex
        with self.sessions.begin() as session:
            session.add(JobPlan(
                id=plan_id, actor_id=int(actor), team_ids=sorted(team_ids),
                source_datasets=sorted(dataset_ids), inventory_hash=fingerprint,
                settings=config, preview=preview, assignments=assignments,
            ))
        return plan_id, preview

    def apply(self, actor, plan_id, fresh_fingerprint):
        with self.sessions.begin() as session:
            plan = session.get(JobPlan, str(plan_id or ''))
            if not plan or plan.state != 'preview' or plan.actor_id != actor:
                raise WorkflowError('Select an unapplied job-distribution preview.')
            if plan.inventory_hash != fresh_fingerprint:
                raise WorkflowError('Source inventory changed. Preview jobs again.')
            self._registered(session, plan.team_ids)
            if session.scalar(select(PreparedJob.id).limit(1)):
                raise WorkflowError('Jobs have already been prepared.')
            created = []
            for reg_id in plan.team_ids:
                for position, contents in enumerate(plan.assignments[reg_id], 1):
                    if not contents:
                        raise WorkflowError('Cannot create an empty labeling job.')
                    jid = f'{plan.id[:20]}-{position}-{reg_id[:24]}'
                    session.add(PreparedJob(
                        id=jid, registration_id=reg_id, position=position,
                        plan_id=plan.id, assets=contents,
                    ))
                    created.append(jid)
            plan.state = 'prepared'
            session.add(Audit(
                team_id=0, batch_id='', actor_id=actor,
                action='prepared_jobs', details={
                    'plan_id': plan.id, 'total_jobs': len(created),
                    'native_jobs_created': False,
                }
            ))
            return {'plan_id': plan.id, 'jobs': len(created),
                    'native_jobs_created': False}

    def overview(self, actor, organiser=False):
        with self.sessions() as session:
            rows = []
            registrations = {r.id:r for r in session.scalars(select(Registration))}
            for job in session.scalars(select(PreparedJob).order_by(
                    PreparedJob.registration_id, PreparedJob.position)):
                reg = registrations.get(job.registration_id)
                if not organiser and (not reg or reg.monitor_id != actor):
                    continue
                rows.append({
                    'id':job.id, 'team_name':reg.team_name if reg else job.registration_id,
                    'registration_id':job.registration_id,
                    'position':job.position, 'state':job.state,
                    'images':sum(a['kind']=='images' for a in job.assets),
                    'videos':sum(a['kind']=='video' for a in job.assets),
                    'effort':sum(a['units'] for a in job.assets),
                    'remote_job_ids':job.remote_job_ids,
                    'revision':job.revision,
                    'submission_count':job.submission_count,
                    'review_note':job.review_note,
                })
            return rows
