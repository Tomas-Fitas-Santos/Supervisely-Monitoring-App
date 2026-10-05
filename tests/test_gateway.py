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


@pytest.mark.parametrize('kind,frame', [('images', -1), ('video', 2)])
def test_real_sdk_renders_selected_annotations_without_remote_writes(kind, frame):
    import base64
    import cv2
    import numpy as np
    import supervisely as sly
    api = Mock()
    api.dataset.get_info_by_id.return_value = NS(project_id=5)
    api.project.get_info_by_id.return_value = NS(id=5, workspace_id=6, type='images' if kind == 'images' else 'videos')
    api.workspace.get_info_by_id.return_value = NS(team_id=101)
    (api.image if kind == 'images' else api.video).get_info_by_id.return_value = NS(dataset_id=50)
    cls = sly.ObjClass('bird', sly.Rectangle, color=[255, 0, 0])
    meta = sly.ProjectMeta(sly.ObjClassCollection([cls]))
    api.project.get_meta.return_value = meta.to_json()
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    box = sly.Rectangle(10, 10, 40, 40)
    if kind == 'images':
        api.image.download_np.return_value = image
        api.annotation.download_json.return_value = sly.Annotation((64, 64), [sly.Label(box, cls)]).to_json()
    else:
        obj = sly.VideoObject(cls)
        figure = sly.VideoFigure(obj, box, 2)
        ann = sly.VideoAnnotation((64, 64), 10, sly.VideoObjectCollection([obj]),
                                  sly.FrameCollection([sly.Frame(2, [figure])]))
        api.video.frame.download_np.return_value = image
        api.video.annotation.download.return_value = ann.to_json()
    batch = NS(id='batch', kind=kind, dataset_id=50, assets=[{'entity_id': 501, 'frames': 10}])
    preview = Gateway(api).review_preview(NS(id=101), batch, 501, frame)
    assert preview['labels'] == 1 and preview['frame_index'] == frame
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(preview['annotated'].split(',')[1]), dtype=np.uint8), 1)
    assert decoded.shape == image.shape and np.any(decoded != 0)
    assert np.all(image == 0)  # Overlay rendering never mutates the downloaded original.
    api.annotation.upload_json.assert_not_called()
    api.labeling_job.create.assert_not_called()


def test_preview_rejects_foreign_entities_and_invalid_frames_before_downloading():
    api = Mock()
    batch = NS(id='batch', kind='video', assets=[{'entity_id': 501, 'frames': 10}])
    for entity, frame in [(999, 2), (501, -1), (501, 10)]:
        with pytest.raises(WorkflowError):
            Gateway(api).review_preview(NS(id=101), batch, entity, frame)
    api.video.frame.download_np.assert_not_called()
    api.dataset.get_info_by_id.assert_not_called()


def test_preview_requires_dataset_ownership():
    api = Mock()
    api.dataset.get_info_by_id.return_value = NS(project_id=5)
    api.project.get_info_by_id.return_value = NS(workspace_id=6, type='images')
    api.workspace.get_info_by_id.return_value = NS(team_id=999)
    with pytest.raises(WorkflowError, match='participant team'):
        Gateway(api).review_preview(NS(id=101), NS(id='b', kind='images', dataset_id=50,
            assets=[{'entity_id': 501}]), 501, -1)
    api.image.download_np.assert_not_called()


def test_source_overview_uses_actual_dataset_counts_and_monitoring_team_only():
    from monitoring.setup_gateway import SetupGateway
    api = Mock()
    api.workspace.get_list.return_value = [NS(id=4, name='Event sources')]
    api.project.get_list.return_value = [NS(id=5, name='Images', type='images'), NS(id=6, name='Ignore', type='point_clouds')]
    api.dataset.get_list.return_value = [NS(id=50, name='Birds', items_count=17)]
    source = SetupGateway(api).sources(10)
    assert source['datasets'][0]['items'] == 17
    api.workspace.get_list.assert_called_once_with(10)
    api.dataset.get_list.assert_called_once_with(5)
    api.team.get_list.assert_not_called()
