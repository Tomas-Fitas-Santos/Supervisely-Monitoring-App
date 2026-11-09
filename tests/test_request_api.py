from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from monitoring.request_api import RequestApi


def test_identity_and_directory_reads_are_deduplicated_within_request_only():
    raw = Mock()
    api = RequestApi(raw)
    for _ in range(3):
        api.user.get_my_info()
        api.user.get_member_info_by_id(10, 99)
        api.user.get_list()
        api.role.get_list()
    for method in (raw.user.get_my_info, raw.user.get_member_info_by_id,
                   raw.user.get_list, raw.role.get_list):
        assert method.call_count == 1
    api.user.get_member_info_by_id(10, 90)
    assert raw.user.get_member_info_by_id.call_count == 2
    fresh = RequestApi(raw)
    fresh.user.get_my_info()
    fresh.user.get_member_info_by_id(10, 99)
    assert raw.user.get_my_info.call_count == 2
    assert raw.user.get_member_info_by_id.call_count == 3


def test_mutations_and_native_reconciliation_are_never_cached():
    raw = Mock()
    api = RequestApi(raw)
    for _ in range(2):
        api.post('members.list', {'teamId': 10})
        api.user.add_to_team(10, 99)
        api.labeling_job.get_info_by_id(5)
    assert raw.post.call_count == 2
    assert raw.user.add_to_team.call_count == 2
    assert raw.labeling_job.get_info_by_id.call_count == 2


def test_failed_reads_are_not_cached():
    raw = Mock()
    raw.user.get_my_info.side_effect = [TimeoutError(), NS(id=99)]
    api = RequestApi(raw)
    with pytest.raises(TimeoutError):
        api.user.get_my_info()
    assert api.user.get_my_info().id == 99
