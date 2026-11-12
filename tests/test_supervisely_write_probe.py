"""The optional real-job probe may only remove its own benchmark jobs."""
from types import SimpleNamespace

import pytest

from annotatiathon.supervisely_write_probe import (
    create_delete_once,
    run,
    validate_test_resources,
)


def obj(**values):
    return SimpleNamespace(**values)


class FakeLabeling:
    def __init__(self):
        self.existing = {
            9: obj(id=9, name="Existing event job", dataset_id=23,
                   assigned_to_id=55),
        }
        self.removed = []
        self.created = []

    def get_list(self, _team, *, dataset_id=None, show_disabled=False):
        assert show_disabled
        return [item for item in self.existing.values()
                if dataset_id is None or item.dataset_id == dataset_id]

    def create(self, *, name, dataset_id, user_ids, images_ids, readme):
        assert name.startswith("Nightjar Benchmark latency probe ")
        assert len(user_ids) == len(images_ids) == 1
        assert "No annotation required" in readme
        result = obj(id=101, name=name, dataset_id=dataset_id,
                     assigned_to_id=user_ids[0])
        self.existing[101] = result
        self.created.append(result)
        return [result]

    def get_info_by_id(self, identifier):
        return self.existing.get(identifier)

    def remove(self, identifier):
        assert identifier != 9
        self.removed.append(identifier)
        del self.existing[identifier]


class FakeApi:
    def __init__(self, project_name="Nightjar Benchmark - temporary"):
        self.labeling_job = FakeLabeling()
        self.workspace = obj(get_info_by_id=lambda _id: obj(team_id=100))
        self.dataset = obj(
            get_info_by_id=lambda _id: obj(id=23, project_id=33),
        )
        self.image = obj(get_info_by_id=lambda _id: obj(id=77, dataset_id=23))
        self.project = obj(
            get_info_by_id=lambda _id: obj(
                id=33, workspace_id=22, name=project_name,
            ),
        )


CONFIG = obj(team_id=100, workspace_id=22)


def test_one_real_job_created_verified_and_deleted_without_touching_other_jobs():
    api = FakeApi()
    summary, profile = run(dataset_id=23, image_id=77, user_id=55,
                           repeats=1, pause_seconds=2, api=api, config=CONFIG)
    assert len(api.labeling_job.created) == 1
    assert api.labeling_job.removed == [101]
    assert list(api.labeling_job.existing) == [9]
    assert summary["real_jobs_created"] == 1
    assert summary["real_jobs_removed_verified"] == 1
    assert summary["real_images_uploaded"] == 0
    assert profile["publish"]["count"] == 1
    assert profile["publish"]["p50_ms"] >= 0


def test_refuses_regular_event_project_before_creating_any_job():
    api = FakeApi(project_name="Nightjar Official Event")
    with pytest.raises(ValueError, match="Refusing writes"):
        validate_test_resources(
            api, CONFIG, dataset_id=23, image_id=77, user_id=55,
        )
    assert not api.labeling_job.created
    assert not api.labeling_job.removed


def test_refuses_image_from_another_dataset():
    api = FakeApi()
    api.image = obj(get_info_by_id=lambda _id: obj(id=77, dataset_id=24))
    with pytest.raises(ValueError, match="mismatch"):
        validate_test_resources(
            api, CONFIG, dataset_id=23, image_id=77, user_id=55,
        )
    assert not api.labeling_job.created


def test_failed_creation_with_remote_success_still_removes_only_probed_job():
    api = FakeApi()
    actual = api.labeling_job.create

    def ambiguous(**kwargs):
        actual(**kwargs)
        raise TimeoutError("Uncertain job creation result")

    api.labeling_job.create = ambiguous
    with pytest.raises(RuntimeError, match="create/verify failed"):
        create_delete_once(
            api, CONFIG, dataset_id=23, image_id=77,
            user_id=55, iteration=1,
        )
    assert api.labeling_job.removed == [101]
    assert list(api.labeling_job.existing) == [9]


def test_refuses_large_or_unbounded_probe():
    with pytest.raises(ValueError, match="1–3"):
        run(dataset_id=23, image_id=77, user_id=55, repeats=10,
            pause_seconds=2, api=FakeApi(), config=CONFIG)
