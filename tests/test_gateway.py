from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from monitoring.gateway import Gateway
from monitoring.service import WorkflowError


def test_independent_nonoverlapping_image_jobs_and_complete_video():
    team = NS(annotator_ids=[11, 12], monitor_id=90)
    batch = NS(kind='images', assets=[{'entity_id': i} for i in range(5)])
    assert Gateway.slices(team, batch) == [(11, [0, 2, 4]), (12, [1, 3])]
    batch.kind = 'video'
    batch.assets = [{'entity_id': 999}]
    assert Gateway.slices(team, batch) == [(11, [999])]


def test_actual_sdk_signatures_support_used_parameters():
    import inspect
    from supervisely.api.labeling_job_api import LabelingJobApi
    from supervisely.api.team_api import TeamApi
    assert {'name', 'dataset_id', 'user_ids', 'images_ids', 'reviewer_id', 'enable_quality_check'} <= set(inspect.signature(LabelingJobApi.create).parameters)
    assert {'team_id', 'filter_job_id', 'start_date'} <= set(inspect.signature(TeamApi.get_activity).parameters)


def test_progress_uses_confirmed_entities_not_annotation_presence():
    api = Mock()
    api.labeling_job.get_info_by_id.return_value = NS(status='in_progress')
    api.labeling_job.get_stats.return_value = {'job': {'imagesCount': 8, 'finishedImagesCount': 3}}
    assert Gateway(api).progress(NS(job_ids=[77])) == {'status': 'in_progress', 'total': 8, 'completed': 3}


def test_frame_acceptance_cannot_accept_entire_video():
    with pytest.raises(WorkflowError, match='cannot mark'):
        Gateway(Mock()).publish_image_review(NS(), NS(kind='video'), 201, 'accepted')


def test_ambiguous_release_name_fails_closed():
    api = Mock()
    api.labeling_job.get_list.return_value = [NS(name='nightjar:a1:u11', assigned_to_id=11)] * 2
    with pytest.raises(WorkflowError, match='Multiple'):
        Gateway(api).find_release(NS(id=1, annotator_ids=[11, 12]),
            NS(dataset_id=31, release_key='nightjar:a1', kind='images', assets=[{'entity_id': 101}]))
