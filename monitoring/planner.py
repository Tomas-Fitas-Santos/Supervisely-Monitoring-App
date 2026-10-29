"""Reproducible effort/stratum balancing; all capacity and replication are inputs."""
from collections import Counter
import hashlib


def allocate(team_ids, assets, replicas=1, batch_units=None, pilot=False, seed=2026,
             common_source_ids=None, require_final_video=False):
    if not team_ids or len(set(team_ids)) != len(team_ids) or type(replicas) is not int or not 1 <= replicas <= len(team_ids):
        raise ValueError('Choose distinct teams and a replication factor between one and the number of teams.')
    if batch_units is None or batch_units <= 0 or len({a['source_id'] for a in assets}) != len(assets):
        raise ValueError('Batch size must be positive and source IDs unique.')
    if not assets or any(a['kind'] not in ('images', 'video') or type(a.get('units')) is not int or a['units'] <= 0 for a in assets):
        raise ValueError('Use image/video assets with positive integer effort units.')
    common = set(common_source_ids or [])
    if common - {a['source_id'] for a in assets}:
        raise ValueError('Common challenge source IDs must be in the selected inventory.')
    if require_final_video and not any(a['kind'] == 'video' for a in assets):
        raise ValueError('The event rule requires a video for each team, but none were selected.')
    assigned = {t: {'images': [], 'videos': []} for t in sorted(team_ids)}
    loads = dict.fromkeys(assigned, 0)
    strata = {t: Counter() for t in assigned}
    def tie(value): return hashlib.sha256(f'{seed}:{value}'.encode()).hexdigest()
    def place(asset):
        count = len(team_ids) if asset['source_id'] in common else replicas
        stratum = str(asset.get('stratum', asset['kind']))
        candidates = sorted(assigned, key=lambda t: (loads[t], strata[t][stratum], tie(f'{asset["source_id"]}:{t}')))[:count]
        for t in candidates:
            assigned[t]['images' if asset['kind'] == 'images' else 'videos'].append(asset)
            loads[t] += asset['units']; strata[t][stratum] += 1
    videos = sorted([a for a in assets if a['kind'] == 'video'], key=lambda a: (-a['units'], tie(a['source_id'])))
    for video in videos: place(video)
    if require_final_video:
        counts = Counter(v['source_id'] for a in assigned.values() for v in a['videos'])
        for t in assigned:
            if not assigned[t]['videos']:
                video = min(videos, key=lambda a: (counts[a['source_id']], a['units'], tie(a['source_id'])))
                assigned[t]['videos'].append(video); loads[t] += video['units']; counts[video['source_id']] += 1
                strata[t][str(video.get('stratum', 'video'))] += 1
    for asset in sorted([a for a in assets if a['kind'] == 'images'], key=lambda a: (-a['units'], tie(a['source_id']))):
        place(asset)
    result = {}
    for t, group in assigned.items():
        batches, current, effort = [], [], 0
        for a in group['images']:
            if current and effort + a['units'] > batch_units:
                batches.append(current); current, effort = [], 0
            current.append(a); effort += a['units']
        if current: batches.append(current)
        result[t] = {'image_batches': batches, 'videos': group['videos'],
                     'video': group['videos'][0] if group['videos'] else None, 'units': loads[t],
                     'strata': dict(strata[t])}
    return result
