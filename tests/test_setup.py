import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select

from monitoring.db import Audit, Batch, SetupOperation, SetupState, Team
from monitoring.planner import allocate
from monitoring.service import WorkflowError
from monitoring.setup import Setup, pilot_meta
from monitoring.setup_gateway import SetupGateway


@pytest.fixture
def setup(service, tmp_path):
    with service.sessions.begin() as s:
        s.execute(delete(Batch))
        s.execute(delete(Team))
    return Setup(service.sessions, 10, tmp_path / 'uploads')


def roster(setup, two=False):
    with setup.sessions.begin() as s:
        s.add(Team(id=101, name='Pilot A', monitor_id=90, annotator_ids=[11, 12]))
        if two:
            s.add(Team(id=102, name='Pilot B', monitor_id=91, annotator_ids=[21, 22]))


def inventory():
    return [{'source_id': f'images:{i}', 'entity_id': i, 'dataset_id': 50, 'project_id': 5,
             'name': f'{i}.jpg', 'kind': 'images', 'frames': None, 'units': 1} for i in [1, 2, 3, 4]]


def gateway():
    gw = Mock()
    gw.inventory.return_value = inventory(), {'50': {'classes': [{'title': 'bird'}]}}
    gw.monitor.side_effect = lambda team, uid: NS(id=uid, login=f'u{uid}')
    gw.copy_group.side_effect = lambda team, key, index, group, meta, annotations, cb: (
        1000 + team + index, [{'source_id': a['source_id'], 'entity_id': 10000 + team * 100 + a['entity_id']} for a in group])
    return gw


def plan(setup, gw, teams=None, replicas=1):
    return setup.preview(gw, 90, [50], teams or [101], replicas, 2, 1, True, False)[0]


def test_pilot_can_use_one_pair_and_images_only():
    result = allocate([101], inventory(), replicas=1, batch_units=2, pilot=True)
    assert len(result[101]['image_batches']) == 2
    assert result[101]['video'] is None
    with pytest.raises(ValueError):
        allocate([101], inventory(), replicas=1, batch_units=2)


def test_entire_video_is_not_split_in_reduced_pilot():
    assets = inventory() + [{'source_id': 'video:9', 'kind': 'video', 'units': 73}]
    result = allocate([101, 102], assets, replicas=1, batch_units=2, pilot=True)
    assert all(a['video']['units'] == 73 for a in result.values())
    assert all(len(a['image_batches']) <= 2 for a in result.values())


def test_preview_has_no_remote_writes_and_maps_all_sources(setup):
    roster(setup, two=True)
    gw = gateway()
    key, preview = setup.preview(gw, 90, [50], [101, 102], 2, 2, 1, True, False)
    assert preview['source_images'] == 4
    assert [t['images'] for t in preview['teams']] == [4, 4]
    gw.copy_group.assert_not_called()
    with setup.sessions() as s:
        assert not list(s.scalars(select(Batch)))
        assert s.get(SetupOperation, key).state == 'planned'


def test_distribution_creates_independent_entities_and_locked_batches_once(setup):
    roster(setup, two=True)
    gw = gateway()
    key = plan(setup, gw, [101, 102], 2)
    setup.distribute(gw, 90, key)
    with setup.sessions() as s:
        batches = list(s.scalars(select(Batch)))
        assert len(batches) == 4
        assert all(b.state == 'locked' and not b.job_ids for b in batches)
        assert len({a['entity_id'] for b in batches for a in b.assets}) == 8
        assert s.get(SetupOperation, key).state == 'completed'
        assert s.get(SetupState, 1).operation_id is None
    with pytest.raises(WorkflowError, match='unapplied'):
        setup.distribute(gw, 90, key)
    assert gw.copy_group.call_count == 4


def test_full_event_replication_rule_remains_enforced(setup):
    roster(setup)
    with pytest.raises(WorkflowError, match='three'):
        setup.preview(gateway(), 90, [50], [101], 1, 2, 1, False, False)


def test_changed_source_inventory_invalidates_preview_without_creating_copies(setup):
    roster(setup)
    gw = gateway()
    key = plan(setup, gw)
    gw.inventory.return_value = inventory()[:-1], {'50': {'classes': [{'title': 'bird'}]}}
    with pytest.raises(WorkflowError, match='inventory'):
        setup.distribute(gw, 90, key)
    gw.copy_group.assert_not_called()


def test_stale_monitor_assignment_invalidates_plan(setup):
    roster(setup)
    gw = gateway()
    key = plan(setup, gw)
    with setup.sessions.begin() as s:
        s.get(Team, 101).monitor_id = 91
    with pytest.raises(WorkflowError, match='roster changed'):
        setup.distribute(gw, 90, key)
    gw.copy_group.assert_not_called()


def test_failed_copy_keeps_checkpoints_blocks_retries_and_publishes_no_batches(setup):
    roster(setup)
    gw = gateway()
    key = plan(setup, gw)
    def fail(*args):
        args[-1]({'dataset_id': 888})
        raise TimeoutError('token=must-not-reach-browser')
    gw.copy_group.side_effect = fail
    with pytest.raises(WorkflowError, match='needs inspection') as error:
        setup.distribute(gw, 90, key)
    assert 'token=' not in str(error.value)
    with setup.sessions() as s:
        assert not list(s.scalars(select(Batch)))
        op = s.get(SetupOperation, key)
        assert op.state == 'needs_inspection'
        assert op.result['resources'] == [{'team_id': 101, 'dataset_id': 888}]
        assert s.get(SetupState, 1).operation_id == key
    with pytest.raises(WorkflowError):
        setup.distribute(gw, 90, key)
    assert gw.copy_group.call_count == 1
    setup.acknowledge(90, key, 'Confirmed the partial dataset in Supervisely and removed the unused copy.')
    assert setup.snapshot()['blocked_by'] is None
    with pytest.raises(WorkflowError):
        setup.distribute(gw, 90, key)


def test_concurrent_setup_serializes_organisers(setup):
    entered, release = Event(), Event()
    def first():
        with setup.operation(90, 'operation_first', 'team', {}):
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(first)
        assert entered.wait(5)
        try:
            with pytest.raises(WorkflowError, match='Another setup'):
                with setup.operation(91, 'operation_second', 'team', {}):
                    pytest.fail('Must not acquire a second setup operation')
        finally:
            release.set()
        future.result()


def test_monitor_reassignment_changes_visibility_and_records_history(setup, service):
    roster(setup)
    gw = gateway()
    setup.assign_monitor(gw, 90, 'assign_operation', 101, 91, 0)
    assert service.snapshot(90)['teams'] == []
    assert service.snapshot(91)['teams'][0]['id'] == 101
    gw.add_monitor.assert_called_once()
    with setup.sessions() as s:
        audit = s.scalar(select(Audit).where(Audit.action == 'monitor_assigned'))
        assert audit.details['previous_monitor_id'] == 90


def test_monitor_reassignment_refuses_active_native_jobs(service):
    setup = Setup(service.sessions, 10)
    service.reserve_release(90, 1, 'a1', 0)
    with pytest.raises(WorkflowError, match='Reassign before releasing'):
        setup.assign_monitor(gateway(), 90, 'assign_operation', 1, 91, 1)


def test_workflow_release_is_blocked_during_setup(service):
    setup = Setup(service.sessions, 10)
    with setup.operation(90, 'setup_operation', 'upload', {}):
        with pytest.raises(WorkflowError, match='setup is running'):
            service.reserve_release(90, 1, 'a1', 0)


def test_new_team_uses_existing_logins_and_selected_monitor(setup):
    gw = gateway()
    gw.api.team.create.return_value = NS(id=101, name='Real pair')
    gw.add_login.side_effect = [NS(id=11), NS(id=12)]
    tid = setup.create_team(gw, 90, 'create_operation', 'Real pair', ['alice', 'bob'], 91)
    assert tid == 101
    assert setup.roster()[0]['annotator_ids'] == [11, 12]
    gw.api.user.create.assert_not_called()
    assert [call.args[1] for call in gw.add_login.call_args_list] == ['alice', 'bob']


def test_registered_pair_cannot_have_same_annotator_twice(setup):
    gw = gateway()
    with pytest.raises(WorkflowError, match='distinct'):
        setup.create_team(gw, 90, 'create_operation', 'Pair', ['alice', 'ALICE'], 91)
    gw.api.team.create.assert_not_called()


def test_setup_requires_current_monitoring_admin():
    api = Mock()
    api.user.get_member_info_by_id.return_value = NS(role='manager')
    with pytest.raises(WorkflowError, match='Admin'):
        SetupGateway(api).organiser(10, 90)
    api.user.get_member_info_by_id.return_value = None
    with pytest.raises(WorkflowError, match='Admin'):
        SetupGateway(api).organiser(10, 90)


def test_add_existing_login_does_not_need_global_user_listing():
    api = Mock()
    api.user.get_member_info_by_login.side_effect = [None, NS(id=11, login='alice', disabled=False)]
    user = SetupGateway(api).add_login(101, 'alice', 7)
    assert user.id == 11
    api.user.add_to_team_by_login.assert_called_once_with('alice', 101, 7)
    api.user.get_list.assert_not_called()


def test_source_dataset_cannot_come_from_participant_team():
    api = Mock()
    api.dataset.get_info_by_id.return_value = NS(project_id=5)
    api.project.get_info_by_id.return_value = NS(workspace_id=6, type='images')
    api.workspace.get_info_by_id.return_value = NS(team_id=101)
    with pytest.raises(WorkflowError, match='inside the monitoring'):
        SetupGateway(api).source_dataset(10, 50)


def test_copy_maps_by_filename_even_if_api_reorders_result():
    api = Mock()
    api.workspace.get_info_by_name.return_value = NS(id=4)
    api.project.create.return_value = NS(id=5)
    api.dataset.create.return_value = NS(id=6)
    api.image.copy_batch.return_value = [NS(id=22, dataset_id=6, name='2.jpg'), NS(id=21, dataset_id=6, name='1.jpg')]
    ds, assets = SetupGateway(api).copy_group(101, 'copy_operation', 1, inventory()[:2], {}, False, Mock())
    assert ds == 6
    assert assets == [{'source_id': 'images:1', 'entity_id': 21}, {'source_id': 'images:2', 'entity_id': 22}]
    api.image.copy_batch.assert_called_once_with(6, [1, 2], with_annotations=False)


def test_chunked_upload_ownership_offsets_and_traversal(setup):
    encode = lambda b: base64.b64encode(b).decode()
    assert setup.stage(90, 'upload_file_a', 'bird.png', 6, 0, encode(b'abc')) == 3
    with pytest.raises(WorkflowError, match='another user'):
        setup.stage(91, 'upload_file_a', 'bird.png', 6, 3, encode(b'def'))
    with pytest.raises(WorkflowError, match='offset changed'):
        setup.stage(90, 'upload_file_a', 'bird.png', 6, 0, encode(b'abc'))
    assert setup.stage(90, 'upload_file_a', 'bird.png', 6, 3, encode(b'def')) == 6
    with pytest.raises(WorkflowError, match='paths'):
        setup.stage(90, 'upload_file_b', '../bird.png', 3, 0, encode(b'abc'))


def test_upload_assembles_chunks_then_cleans_temporary_media(setup):
    encode = lambda b: base64.b64encode(b).decode()
    setup.stage(90, 'upload_file_a', 'bird.png', 6, 0, encode(b'abc'))
    setup.stage(90, 'upload_file_a', 'bird.png', 6, 3, encode(b'def'))
    gw = gateway()
    gw.api.workspace.get_info_by_id.return_value = NS(id=4, team_id=10)
    def upload(team, workspace, name, kind, paths, meta, checkpoint):
        assert paths[0][1].read_bytes() == b'abcdef'
        assert len(meta['classes']) == 5
        checkpoint({'dataset_id': 6})
        return {'dataset_id': 6}
    gw.upload.side_effect = upload
    assert setup.upload(gw, 90, 'upload_operation', ['upload_file_a'], 4, 'Bird source', 'images', None)['dataset_id'] == 6
    assert not (setup.upload_dir / 'upload_file_a').exists()
    with pytest.raises(WorkflowError, match='fully uploaded'):
        setup.upload(gw, 90, 'upload_again', ['upload_file_a'], 4, 'Bird source', 'images', None)


def test_incomplete_or_foreign_upload_cannot_create_a_remote_project(setup):
    setup.stage(90, 'upload_file_a', 'bird.png', 6, 0, base64.b64encode(b'abc').decode())
    gw = gateway()
    with pytest.raises(WorkflowError, match='fully uploaded'):
        setup.upload(gw, 90, 'upload_operation', ['upload_file_a'], 4, 'Bird source', 'images', None)
    gw.upload.assert_not_called()


def test_pilot_schema_round_trips_sdk():
    import supervisely as sly
    meta = sly.ProjectMeta.from_json(pilot_meta())
    assert [c.name for c in meta.obj_classes] == ['bird', 'crown', 'left_eye', 'right_eye', 'beak']
    assert meta.tag_metas.get('visibility').possible_values == ['visible', 'occluded']


def test_sdk_supports_provisioning_calls():
    import inspect
    from supervisely.api.user_api import UserApi
    from supervisely.api.workspace_api import WorkspaceApi
    from supervisely.api.image_api import ImageApi
    from supervisely.api.video.video_api import VideoApi
    assert {'user_login', 'team_id', 'role_id'} <= set(inspect.signature(UserApi.add_to_team_by_login).parameters)
    assert {'user_id', 'team_id', 'role_id'} <= set(inspect.signature(UserApi.change_team_role).parameters)
    assert {'parent_id', 'name'} <= set(inspect.signature(WorkspaceApi.get_info_by_name).parameters)
    for cls in [ImageApi, VideoApi]:
        assert {'dst_dataset_id', 'ids', 'with_annotations'} <= set(inspect.signature(cls.copy_batch).parameters)
        assert {'dataset_id', 'names', 'paths'} <= set(inspect.signature(cls.upload_paths).parameters)


def test_uppercase_upload_extensions_are_normalized_for_sdk(setup):
    setup.stage(90, 'upload_video_a', 'Bird.MP4', 3, 0, base64.b64encode(b'abc').decode())
    gw = gateway()
    gw.api.workspace.get_info_by_id.return_value = NS(id=4, team_id=10)
    def upload(team, workspace, name, kind, paths, meta, checkpoint):
        from pathlib import Path
        assert paths[0][0] == 'Bird.mp4'
        assert Path(paths[0][0]).suffix == paths[0][1].suffix
        return {'dataset_id': 6}
    gw.upload.side_effect = upload
    setup.upload(gw, 90, 'upload_operation', ['upload_video_a'], 4, 'Video source', 'video', None)


def test_live_setup_rejects_synthetic_demo_database(setup):
    roster(setup)
    with setup.sessions.begin() as s:
        s.add(Batch(id='demo-1-images', team_id=101, position=1, kind='images', dataset_id=999,
            assets=[{'source_id': 'demo-image-1', 'entity_id': 101}]))
    with pytest.raises(WorkflowError, match='fresh pilot.db'):
        setup.authorize(gateway(), 90)
