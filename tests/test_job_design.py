"""Whole-video allocation, overlap, job count and reproducibility contracts."""
from collections import Counter

import pytest

from monitoring.job_design import plan_jobs
from monitoring.service import WorkflowError


def inventory(images=20, videos=6):
    return [
        {'source_id':f'images:{i}', 'dataset_id':10, 'kind':'images',
         'units':1,'stratum':f'species{i%3}'}
        for i in range(images)
    ] + [
        {'source_id':f'video:{i}', 'dataset_id':11, 'kind':'video',
         'units':60+(i*10),'frames':60+(i*10),'stratum':f'species{i%2}'}
        for i in range(videos)
    ]


def settings(**overrides):
    return {
        'jobs_per_team':3, 'videos_per_team':2, 'image_replicas':2,
        'video_replicas':2, 'image_common_percent':20,
        'seed':23, 'image_effort':1, **overrides
    }


def all_assigned(assignments):
    return {team:[a for group in groups for a in group]
            for team, groups in assignments.items()}


def test_four_teams_get_exact_jobs_whole_videos_and_configured_overlap():
    teams = ['A','B','C','D']
    output, preview = plan_jobs(teams, inventory(), settings())
    assert preview['total_jobs'] == 12
    assert preview['source_videos'] == 6
    assert preview['common_images'] == 4
    assert sorted(output) == teams
    assert all(len(jobs) == 3 for jobs in output.values())
    assert all(all(len(job)>0 for job in jobs) for jobs in output.values())
    flattened = all_assigned(output)
    assert all(sum(a['kind']=='video' for a in items)==2
               for items in flattened.values())
    assert all(len({a['source_id'] for a in items}) == len(items)
               for items in flattened.values())
    video_cover = Counter(a['source_id'] for items in flattened.values()
                          for a in items if a['kind']=='video')
    assert all(amount<=2 for amount in video_cover.values())
    image_cover = Counter(a['source_id'] for items in flattened.values()
                          for a in items if a['kind']=='images')
    assert sum(amount==4 for amount in image_cover.values()) == 4
    assert sum(amount==2 for amount in image_cover.values()) == 16


def test_demo_one_pair_uses_same_planner():
    config = settings(jobs_per_team=3, videos_per_team=1,
                      video_replicas=1, image_replicas=1,
                      image_common_percent=100)
    assignments, preview = plan_jobs(['pair-1'],inventory(8,2),config)
    assert preview['total_jobs'] == 3
    assert sum(len(x) for x in assignments['pair-1']) == 9
    assert sum(a['kind']=='video' for x in assignments['pair-1'] for a in x) == 1


def test_seed_produces_reproducible_results():
    a, report_a = plan_jobs(['A','B','C'],inventory(30,4),
                            settings(jobs_per_team=4,videos_per_team=1,
                                     image_replicas=2,video_replicas=1))
    b, report_b = plan_jobs(['A','B','C'],inventory(30,4),
                            settings(jobs_per_team=4,videos_per_team=1,
                                     image_replicas=2,video_replicas=1))
    assert a==b and report_a==report_b


def test_infeasible_video_and_job_constraints_fail_early():
    with pytest.raises(WorkflowError,match='Video overlap'):
        plan_jobs(['A','B','C','D'], inventory(6,2),
                  settings(videos_per_team=2, video_replicas=1))
    with pytest.raises(WorkflowError,match='labeling jobs'):
        plan_jobs(['A'], inventory(1,1),
                  settings(jobs_per_team=5,videos_per_team=1,
                           image_replicas=1,video_replicas=1))


@pytest.mark.parametrize('changes',[
    {'image_replicas':0},
    {'video_replicas':0},
    {'image_common_percent':101},
    {'jobs_per_team':0},
    {'videos_per_team':101},
    {'seed':-1},
])
def test_invalid_configuration_rejected(changes):
    with pytest.raises(WorkflowError):
        plan_jobs(['A','B'], inventory(20,4), settings(**changes))
