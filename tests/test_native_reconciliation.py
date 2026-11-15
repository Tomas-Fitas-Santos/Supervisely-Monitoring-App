"""Real-provider naming and cache drift must not duplicate or reopen work."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_native_dispatch import configured, launch
from annotatiathon.models import LabelingJob, NativeJobBinding, NativeOperation, SuperviselyConfig
from annotatiathon.native_worker import process_once
from annotatiathon.native_status_cache import refresh_idle_bindings


@pytest.mark.parametrize("uncertain", [False, True])
def test_provider_name_suffix_reconciles_without_duplicate(configured, uncertain):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    original_create = sdk.labeling_job.create

    def suffixed_create(**kwargs):
        jobs = original_create(**kwargs)
        for job in jobs:
            job.name += " (#2)"
            job.entities = [{"id": image_id} for image_id in job.image_ids]
        if uncertain:
            raise ConnectionError("Lost response after provider renamed the job")
        return jobs

    sdk.labeling_job.create = suffixed_create
    with maker() as db:
        config = db.get(SuperviselyConfig, 1)
        result = process_once(db, sdk, config)
        count = len(sdk.jobs)
        if uncertain:
            assert result["status"] == "attention"
            op = db.get(NativeOperation, result["operation_id"])
            op.status = "pending"
            db.commit()
            result = process_once(db, sdk, config)
        assert result["status"] == "done"
        assert len(sdk.jobs) == count
        assert len(list(db.scalars(select(NativeJobBinding)))) == count


def test_suffix_with_wrong_frames_is_not_adopted(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    original_create = sdk.labeling_job.create

    def wrong_images(**kwargs):
        jobs = original_create(**kwargs)
        for job in jobs:
            job.name += " (#3)"
            job.entities = [{"id": -1}]
        return jobs

    sdk.labeling_job.create = wrong_images
    with maker() as db:
        result = process_once(db, sdk, db.get(SuperviselyConfig, 1))
        assert result["status"] == "attention"
        assert db.get(NativeOperation, result["operation_id"]).error_code == "native_job_media_mismatch"
        assert not list(db.scalars(select(NativeJobBinding)))


@pytest.mark.parametrize("status", ["on_review", "needs_correction", "completed"])
def test_idle_status_refresh_preserves_local_decision(configured, status):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        binding = db.scalar(select(NativeJobBinding))
        job = db.get(LabelingJob, binding.job_id)
        job.status, job.review_status = status, "returned_incomplete"
        binding.last_synced_at = 1
        native = next(j for j in sdk.jobs if j.id == binding.native_job_id)
        native.status = "stopped"
        db.commit()
        assert refresh_idle_bindings(db, sdk) == 1
        db.refresh(binding); db.refresh(job)
        assert binding.native_status == "stopped" and binding.last_synced_at > 1
        assert (job.status, job.review_status) == (status, "returned_incomplete")
        assert refresh_idle_bindings(db, sdk) == 0


def test_idle_refresh_does_not_overwrite_replaced_binding(configured):
    http, maker, csrf, plan_id, sdk = configured
    launch(http, csrf, plan_id)
    with maker() as db:
        assert process_once(db, sdk, db.get(SuperviselyConfig, 1))["status"] == "done"
        binding = db.scalar(select(NativeJobBinding))
        job = db.get(LabelingJob, binding.job_id)
        job.status = "needs_correction"
        binding.last_synced_at = 1
        bid, dataset_id = binding.id, job.native_dataset_id
        db.commit()

        def replaced(native_id):
            with maker.begin() as writer:
                changed = writer.get(NativeJobBinding, bid)
                changed.native_job_id = 99999
                changed.native_status = "pending"
            return SimpleNamespace(id=native_id,dataset_id=dataset_id,status="stopped")

        sdk.labeling_job.get_info_by_id = replaced
        assert refresh_idle_bindings(db, sdk) == 0
        db.refresh(binding)
        assert (binding.native_job_id, binding.native_status) == (99999, "pending")
