"""Deterministic workload allocation, before copying assets into participant teams."""
from collections import Counter


def allocate(team_ids: list[int], assets: list[dict], replicas=3, batch_units=None, pilot=False):
    if len(set(team_ids)) != len(team_ids) or len(team_ids) < replicas or replicas < (1 if pilot else 3):
        raise ValueError("Need distinct teams and at least three independent replicas.")
    if batch_units is None or batch_units <= 0 or len({a['source_id'] for a in assets}) != len(assets):
        raise ValueError("Batch size must be positive and source IDs unique.")
    images = [a for a in assets if a['kind'] == 'images']
    videos = [a for a in assets if a['kind'] == 'video']
    if not assets or len(images) + len(videos) != len(assets) or (not videos and not pilot):
        raise ValueError("Use images/video assets and include at least one video.")
    if len(videos) * replicas > len(team_ids):
        raise ValueError("Too few teams for three replicas of each video and one video per team.")
    if any(not isinstance(a.get('units'), int) or a['units'] <= 0 for a in assets):
        raise ValueError("Work units must be positive integers (video frame counts or pilot estimates).")
    assigned = {t: {'images': [], 'video': None} for t in sorted(team_ids)}
    load = dict.fromkeys(assigned, 0)
    free = list(assigned)
    # Longer videos first; extra teams receive additional independent versions.
    ordered_videos = sorted(videos, key=lambda a: (-a['units'], a['source_id']))
    for video in ordered_videos:
        for _ in range(replicas):
            t = free.pop(0)
            assigned[t]['video'] = video
            load[t] += video['units']
    counts = Counter(v['video']['source_id'] for v in assigned.values() if v['video'])
    for t in free if videos else []:
        video = min(ordered_videos, key=lambda a: (counts[a['source_id']], a['units'], a['source_id']))
        assigned[t]['video'] = video
        load[t] += video['units']
        counts[video['source_id']] += 1
    for image in sorted(images, key=lambda a: (-a['units'], a['source_id'])):
        for t in sorted(load, key=lambda t: (load[t], t))[:replicas]:
            assigned[t]['images'].append(image)
            load[t] += image['units']
    result = {}
    for t, group in assigned.items():
        batches, current, units = [], [], 0
        for image in group['images']:
            if current and units + image['units'] > batch_units:
                batches.append(current)
                current, units = [], 0
            current.append(image)
            units += image['units']
        if current:
            batches.append(current)
        result[t] = {'image_batches': batches, 'video': group['video'], 'units': load[t]}
    return result
