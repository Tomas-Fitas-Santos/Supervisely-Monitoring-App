"""Native role editing uses documented member role IDs and verifies writes."""
import pytest

from annotatiathon.models import SuperviselyConfig
from annotatiathon.supervisely_client import (
    NativeAPIError,
    NativeMember,
    change_member_role,
)


def test_role_change_queries_instance_role_id_and_verifies_members(monkeypatch):
    from annotatiathon import supervisely_client as native

    config = SuperviselyConfig(team_id=125980, server_url="https://app.supervisely.com")
    state = {"target_role": "annotator"}
    methods = []

    monkeypatch.setattr(
        native, "require_connection",
        lambda _config: ("https://app.supervisely.com", "DUMMY_ROLE_TEST_TOKEN"),
    )

    def fake_request(client, base, token, method, payload):
        assert base == "https://app.supervisely.com"
        assert token == "DUMMY_ROLE_TEST_TOKEN"
        methods.append((method, payload))
        if method == "users.me":
            return {"id": 123550}
        if method == "roles.list":
            return {"entities": [
                {"id": 1, "role": "admin"},
                {"id": 8, "role": "manager"},
                {"id": 3, "role": "annotator"},
            ]}
        if method == "members.editInfo":
            assert payload == {"id": 55111, "teamId": 125980, "roleId": 8}
            state["target_role"] = "manager"
            return {"ok": True}
        raise AssertionError(method)

    def fake_members(client, base, token, team_id):
        assert team_id == 125980
        return (
            NativeMember(id=123550, login="owner", email=None, role="admin"),
            NativeMember(id=55111, login="member", email=None,
                         role=state["target_role"]),
        )

    monkeypatch.setattr(native, "_request_json", fake_request)
    monkeypatch.setattr(native, "_read_member_by_id",
        lambda client, base, token, team_id, user_id: next(
            (m for m in fake_members(client, base, token, team_id) if m.id == user_id),
            None,
        ))
    changed = change_member_role(
        config, user_id=55111, target_role="manager", expected_role="annotator",
    )
    assert changed.role == "manager"
    assert [method for method, _ in methods] == [
        "users.me", "roles.list", "members.editInfo",
    ]


def test_role_change_protects_owner_before_any_mutation(monkeypatch):
    from annotatiathon import supervisely_client as native

    config = SuperviselyConfig(team_id=125980, server_url="https://app.supervisely.com")
    monkeypatch.setattr(native, "require_connection",
                        lambda _config: ("https://app.supervisely.com", "TOKEN"))
    methods = []

    def fake_request(_client, _base, _token, method, _payload):
        methods.append(method)
        return {"id": 123550}

    monkeypatch.setattr(native, "_request_json", fake_request)
    monkeypatch.setattr(native, "_read_member_by_id",
        lambda *_args: NativeMember(
            id=123550, login="owner", email=None, role="admin",
        ))
    with pytest.raises(NativeAPIError, match="admin accounts cannot be demoted"):
        change_member_role(
            config, user_id=123550, target_role="annotator",
            expected_role="admin",
        )
    assert methods == ["users.me"]


def test_role_change_rejects_stale_identity_before_write(monkeypatch):
    from annotatiathon import supervisely_client as native

    config = SuperviselyConfig(team_id=125980, server_url="https://app.supervisely.com")
    monkeypatch.setattr(native, "require_connection",
                        lambda _config: ("https://app.supervisely.com", "TOKEN"))
    methods = []

    def fake_request(_client, _base, _token, method, _payload):
        methods.append(method)
        return {"id": 123550}

    monkeypatch.setattr(native, "_request_json", fake_request)
    monkeypatch.setattr(native, "_read_member_by_id",
        lambda _client, _base, _token, _team_id, user_id: (
            NativeMember(id=123550, login="owner", email=None, role="admin")
            if user_id == 123550 else
            NativeMember(id=555, login="alice", email=None, role="viewer")
        ))
    with pytest.raises(NativeAPIError, match="role changed since"):
        change_member_role(
            config, user_id=555, target_role="manager",
            expected_role="annotator",
        )
    assert methods == ["users.me"]


def test_targeted_member_lookup_uses_documented_id_filter(monkeypatch):
    from annotatiathon import supervisely_client as native

    def fake_request(_client, _base, _token, method, payload):
        assert method == "members.list"
        assert payload["teamId"] == 125980
        assert payload["filter"] == [{"field": "id", "operator": "=", "value": 555}]
        return {"entities": [{"id": 555, "login": "alice",
                              "role": "manager", "email": "ALICE@example.org"}]}

    monkeypatch.setattr(native, "_request_json", fake_request)
    found = native._read_member_by_id(None, "https://app.supervisely.com",
                                      "TOKEN", 125980, 555)
    assert found.id == 555
    assert found.role == "manager"
    assert found.email == "alice@example.org"
