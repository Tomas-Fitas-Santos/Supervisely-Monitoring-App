"""Regression checks for already-joined Supervisely members and manual jobs."""
import pytest

from annotatiathon.models import Person
from annotatiathon.native_operations_api import native_identity_assignable
from annotatiathon.supervisely_client import (
    NativeAPIError,
    NativeMember,
    _enrich_member_emails,
)


def test_recover_email_from_verified_member_id(monkeypatch):
    from annotatiathon import supervisely_client as client

    def fake_request(_http, _base, _token, action, payload):
        assert action == "users.info"
        users = {
            42: {"id": 42, "login": "RealLogin",
                 "email": "MEMBER@example.org"},
            43: {"id": 43, "login": "SecondLogin",
                 "email": "second@example.org"},
        }
        return users[payload["id"]]

    monkeypatch.setattr(client, "_request_json", fake_request)
    members = (
        NativeMember(id=42, login="RealLogin", email=None, role="annotator"),
        NativeMember(id=43, login="SecondLogin", email=None, role="manager"),
    )
    recovered = _enrich_member_emails(
        "https://app.supervisely.com", "DUMMY_TOKEN", members,
    )
    assert [m.email for m in recovered] == [
        "member@example.org", "second@example.org",
    ]
    assert [m.id for m in recovered] == [42, 43]


def test_unavailable_email_keeps_team_member_unmatched(monkeypatch):
    from annotatiathon import supervisely_client as client

    def unavailable(*_args):
        raise NativeAPIError("unauthorised", "No access")

    monkeypatch.setattr(client, "_request_json", unavailable)
    member = NativeMember(id=44, login="NativeLogin",
                          email=None, role="annotator")
    assert _enrich_member_emails(
        "https://app.supervisely.com", "DUMMY_TOKEN", (member,),
    ) == (member,)


@pytest.mark.parametrize("role", ["annotator", "manager", "admin", "developer"])
def test_joined_monitor_can_be_assigned_a_job_with_verified_id(role):
    person = Person(
        event_id="event", email="monitor@example.org", name="Monitor",
        kind="monitor", membership_status="joined", supervisely_user_id=8765,
        supervisely_role=role, native_verified_at=123456,
    )
    assert native_identity_assignable(person)


def test_joined_status_without_native_id_does_not_unlock_assignment():
    person = Person(
        event_id="event", email="member@example.org", name="Member",
        kind="participant", membership_status="joined",
        supervisely_user_id=None, supervisely_role="annotator",
        native_verified_at=123456,
    )
    assert not native_identity_assignable(person)
