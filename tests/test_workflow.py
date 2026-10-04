from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from monitoring import controller
from monitoring.db import Audit, Batch, Review, Team, now
from monitoring.service import WorkflowError


def revision(service):
    return service.snapshot(90)['teams'][0]['revision']


def submitted(service, batch_id='a1'):
    with service.sessions.begin() as s:
        b = s.get(Batch, batch_id)
        b.state, b.job_ids = 'active', [77]
        b.remote_status, b.synced_at = 'on_review', now()
        b.total = b.completed = len(b.assets)


def test_assignment_is_enforced_on_reads_and_mutations(service):
    assert [t['id'] for t in service.snapshot(90)['teams']] == [1]
    assert service.snapshot(999)['teams'] == []
    with pytest.raises(WorkflowError):
        service.reserve_release(91, 1, 'a1', 0)
    with pytest.raises(WorkflowError):
        service.spec(90, 1, 'b1')


def test_only_one_concurrent_release_reservation(service):
    def attempt():
        try:
            service.reserve_release(90, 1, 'a1', 0)
            return 'reserved'
        except WorkflowError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ['conflict', 'reserved']
    with service.sessions() as s:
        assert len(list(s.scalars(select(Audit)))) == 1


def test_only_one_concurrent_release_confirmation(service):
    service.reserve_release(90, 1, 'a1', 0)
    def attempt():
        try:
            service.finish_release(90, 1, 'a1', [77])
            return 'confirmed'
        except WorkflowError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ['confirmed', 'conflict']


def test_future_batches_cannot_skip_approval(service):
    with pytest.raises(WorkflowError):
        service.reserve_release(90, 1, 'av', 0)
    service.reserve_release(90, 1, 'a1', 0)
    service.finish_release(90, 1, 'a1', [77])
    with pytest.raises(WorkflowError):
        service.reserve_release(90, 1, 'a2', revision(service))


def test_approval_requires_submission_sample_and_fresh_sync(service):
    submitted(service)
    with pytest.raises(WorkflowError):
        service.approve(90, 1, 'a1', revision(service), 'Sample checked')
    service.review(90, 1, 'a1', revision(service), 101, -1, 'accepted', 'All four points checked')
    with service.sessions.begin() as s:
        s.get(Batch, 'a1').synced_at = now() - 1000
    with pytest.raises(WorkflowError):
        service.approve(90, 1, 'a1', revision(service), 'Sample checked')
    service.update_progress('a1', {'status': 'on_review', 'completed': 2, 'total': 2})
    service.approve(90, 1, 'a1', revision(service), 'Sample checked')
    service.reserve_release(90, 1, 'a2', revision(service))


def test_correction_blocks_release_and_history_is_preserved(service):
    submitted(service)
    service.review(90, 1, 'a1', revision(service), 101, -1, 'correction', 'Fix crown')
    with pytest.raises(WorkflowError):
        service.approve(90, 1, 'a1', revision(service), 'Sample checked')
    service.review(90, 1, 'a1', revision(service), 101, -1, 'accepted', 'Crown fixed')
    service.approve(90, 1, 'a1', revision(service), 'Checked fixes')
    service.review(90, 1, 'a1', revision(service), 102, -1, 'correction', 'New issue in past batch')
    with pytest.raises(WorkflowError):
        service.reserve_release(90, 1, 'a2', revision(service))
    with service.sessions() as s:
        assert len(list(s.scalars(select(Review)))) == 2
        assert len(list(s.scalars(select(Audit).where(Audit.action == 'review')))) == 3


def test_video_requires_valid_explicit_frame_index(service):
    submitted(service, 'av')
    for invalid in (-1, 90):
        with pytest.raises(WorkflowError):
            service.review(90, 1, 'av', revision(service), 201, invalid, 'accepted', 'Checked')
    service.review(90, 1, 'av', revision(service), 201, 89, 'accepted', 'Last frame checked')
    with pytest.raises(WorkflowError):
        service.review(90, 1, 'av', revision(service), 999, 0, 'accepted', 'Checked')


def test_progress_failure_retains_successful_values(service):
    submitted(service)
    service.sync_failed('a1')
    b = service.snapshot(90)['teams'][0]['batches'][0]
    assert b['completed'] == 2 and b['sync_error']


def test_remote_timeout_does_not_trigger_duplicate_creation(service):
    class UncertainGateway:
        calls = 0
        def validate(self, *_): pass
        def find_release(self, *_): return []
        def create_release(self, *_):
            self.calls += 1
            raise TimeoutError('Remote may already have created jobs')
    g = UncertainGateway()
    with pytest.raises(WorkflowError, match='uncertain'):
        controller.release(service, g, 90, 1, 'a1', 0)
    with pytest.raises(WorkflowError):
        controller.release(service, g, 90, 1, 'a1', revision(service))
    assert g.calls == 1
    assert service.snapshot(90)['teams'][0]['batches'][0]['state'] == 'releasing'


def test_reconcile_partial_jobs_never_recreates(service):
    service.reserve_release(90, 1, 'a1', 0)
    class PartialGateway:
        def find_release(self, *_): return [77]
        def slices(self, *_): return [(11, [101]), (12, [102])]
    with pytest.raises(WorkflowError, match='Not all jobs'):
        controller.reconcile(service, PartialGateway(), 90, 1, 'a1')


def test_concurrent_tabs_cannot_overwrite_a_newer_review(service):
    submitted(service)
    service.review(90, 1, 'a1', 0, 101, -1, 'correction', 'Fix beak')
    with pytest.raises(WorkflowError, match='Another action'):
        service.review(90, 1, 'a1', 0, 101, -1, 'accepted', 'Stale tab')


def test_incomplete_images_and_unsubmitted_job_block_approval(service):
    submitted(service)
    service.review(90, 1, 'a1', 0, 101, -1, 'accepted', 'Checked')
    service.update_progress('a1', {'status': 'on_review', 'completed': 1, 'total': 2})
    with pytest.raises(WorkflowError, match='Not all'):
        service.approve(90, 1, 'a1', revision(service), 'Checked')
    service.update_progress('a1', {'status': 'on_review,in_progress', 'completed': 2, 'total': 2})
    with pytest.raises(WorkflowError, match='submitted'):
        service.approve(90, 1, 'a1', revision(service), 'Checked')
