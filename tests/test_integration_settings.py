import pytest
from pydantic import ValidationError

from annotatiathon.integration_settings import (
    SuperviselyConnectionStatus,
    SuperviselySettings,
)


def test_supervisely_settings_accept_explicit_team_and_workspace() -> None:
    settings = SuperviselySettings(team_id=125980, workspace_id=3001)
    assert settings.server_url == "https://app.supervisely.com"
    assert settings.team_id == 125980
    assert settings.workspace_id == 3001


@pytest.mark.parametrize(
    "invalid",
    [
        "http://app.supervisely.com",
        "https://user:password@app.supervisely.com",
        "https://app.supervisely.com/api",
        "https://app.supervisely.com?token=abc",
    ],
)
def test_supervisely_settings_reject_unsafe_server_urls(invalid: str) -> None:
    with pytest.raises(ValidationError):
        SuperviselySettings(server_url=invalid)


def test_supervisely_settings_reject_nonpositive_ids() -> None:
    with pytest.raises(ValidationError):
        SuperviselySettings(team_id=0)
    with pytest.raises(ValidationError):
        SuperviselySettings(workspace_id=-1)


def test_public_status_never_contains_api_credentials() -> None:
    status = SuperviselyConnectionStatus(
        settings=SuperviselySettings(team_id=125980),
        credential_configured=True,
        verified=False,
    )
    assert "token" not in status.model_dump_json().lower()
    with pytest.raises(ValidationError):
        SuperviselySettings(team_id=125980, api_token="secret")
