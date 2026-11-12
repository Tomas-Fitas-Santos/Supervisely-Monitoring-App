"""The unattended live calibration is strictly read-only and globally bounded."""
from types import SimpleNamespace

from annotatiathon.supervisely_probe import (
    READ_ONLY_OPERATIONS,
    aggregate,
    planned_requests,
)


def test_probe_only_uses_allowlisted_read_endpoints_and_existing_ids():
    config = SimpleNamespace(team_id=125980, workspace_id=2001)
    requests = planned_requests(
        config, [101, 102, 103, 104, 105], [501, 502, 503, 504, 505], 4,
    )
    assert len(requests) == 11
    assert {endpoint for _group, endpoint, _payload in requests} == {
        "users.me", "teams.info", "workspaces.info", "jobs.info", "annotations.info",
    }
    assert all(endpoint in READ_ONLY_OPERATIONS for _, endpoint, _ in requests)
    assert not any("add" in endpoint or "upload" in endpoint or "restart" in endpoint
                   for _, endpoint, _ in requests)
    assert sum(group == "status" for group, _name, _body in requests) == 4
    assert sum(group == "annotation" for group, _name, _body in requests) == 4
    assert requests[3] == ("status", "jobs.info", {"id": 101})


def test_probe_needs_no_job_and_does_not_invent_status_latency():
    config = SimpleNamespace(team_id=125980, workspace_id=2001)
    items = planned_requests(config, [], [], 3)
    assert len(items) == 3
    assert all(group == "lookup" for group, _name, _ in items)
    measurements = aggregate({"lookup": [20.0, 40.0, 80.0], "status": []})
    assert set(measurements) == {"lookup"}
    assert measurements["lookup"]["p95_ms"] == 80
    assert measurements["lookup"]["count"] == 3
    assert "publish" not in measurements
