"""Public, non-secret Supervisely connection settings contracts.

A Supervisely API token is never part of these public request/response models.
The organiser-only credential endpoint will be added only after staff auth and
encrypted secret storage are implemented.
"""
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SuperviselySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_url: str = "https://app.supervisely.com"
    team_id: int | None = Field(default=None, gt=0)
    workspace_id: int | None = Field(default=None, gt=0)

    @field_validator("server_url")
    @classmethod
    def validate_server_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        parts = urlsplit(cleaned)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
            or parts.port not in (None, 443)
        ):
            raise ValueError("Supervisely server must be an HTTPS origin without credentials or path.")
        return cleaned

    @model_validator(mode="after")
    def ensure_workspace_belongs_to_configured_team(self) -> "SuperviselySettings":
        # Database/API validation of actual workspace ownership belongs in the
        # authenticated connection-test operation. Syntactic IDs alone cannot prove it.
        return self


class SuperviselyConnectionStatus(BaseModel):
    """Safe to display to authorised staff; deliberately contains no token."""

    model_config = ConfigDict(extra="forbid")

    settings: SuperviselySettings
    credential_configured: bool = False
    verified: bool = False
    last_checked_at: str | None = None
    last_error_code: str | None = None
