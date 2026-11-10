"""Scoped Supervisely REST adapter following published SDK endpoint contracts.

Uses official POST APIs for verified reads and explicit admin-only native
role edits. Credentials never leave the server.
"""
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import get_settings
from .credentials import decrypt_token
from .models import SuperviselyConfig


class NativeAPIError(Exception):
    """Safe, non-secret error code surfaced by the application."""

    def __init__(self, code: str, description: str):
        self.code = code
        super().__init__(description)


@dataclass(frozen=True)
class NativeMember:
    id: int
    login: str
    email: str | None
    role: str


@dataclass(frozen=True)
class NativeTeamSnapshot:
    team_id: int
    team_name: str
    workspace_id: int | None
    account_id: int
    account_login: str
    members: tuple[NativeMember, ...]

    def digest(self) -> str:
        members = sorted(
            [(m.id, m.login, m.email, m.role) for m in self.members],
            key=lambda item: item[0],
        )
        data = {"team_id": self.team_id, "workspace_id": self.workspace_id,
                "account_id": self.account_id, "account_login": self.account_login,
                "members": members}
        return hashlib.sha256(
            json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()


def require_connection(config: SuperviselyConfig | None) -> tuple[str, str]:
    settings = get_settings()
    if not config or not config.team_id:
        raise NativeAPIError("missing_team", "Save a Supervisely team ID in Settings first.")
    url = urlsplit(config.server_url)
    allowed = {
        host.strip().lower() for host in settings.supervisely_allowed_hosts.split(",")
        if host.strip()
    }
    if (url.scheme != "https" or not url.hostname
            or url.hostname.lower() not in allowed
            or url.port not in (None, 443)
            or url.path or url.query or url.fragment or url.username or url.password):
        raise NativeAPIError(
            "untrusted_host",
            "Supervisely origin is not allowlisted. Configure SUPERVISELY_ALLOWED_HOSTS "
            "on the server for a trusted enterprise instance.",
        )
    if not config.token_ciphertext:
        raise NativeAPIError(
            "missing_token", "An organiser must save a Supervisely token under Settings.",
        )
    try:
        token = decrypt_token(config.token_ciphertext)
    except ValueError as exc:
        raise NativeAPIError(
            "credential_unavailable", "Saved Supervisely credential cannot be decrypted.",
        ) from exc
    return config.server_url.rstrip("/"), token.strip()


def _request_json(client: httpx.Client, base: str, token: str,
                  method: str, payload: dict) -> dict:
    # Do not expose the response body, URL or underlying HTTP exception to users.
    try:
        response = client.post(
            base + "/public/api/v3/" + method,
            json=payload,
            headers={"x-api-key": token, "Accept": "application/json"},
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise NativeAPIError("timeout", "Supervisely timed out; retry later.") from exc
    except httpx.RequestError as exc:
        raise NativeAPIError(
            "network", "Could not reach the configured Supervisely instance."
        ) from exc
    if response.status_code in (401, 403):
        raise NativeAPIError(
            "unauthorised", "Supervisely rejected the credential or team access."
        )
    if response.status_code == 404:
        raise NativeAPIError("not_found", "Supervisely resource was not found.")
    if response.status_code == 429:
        raise NativeAPIError("rate_limited", "Supervisely rate limit reached.")
    # Supervisely's official UserApi.change_team_role ignores the response
    # body of members.editInfo. Accept success with an empty body, then rely
    # on the mandatory read-back to confirm the actual member role.
    if method == "members.editInfo" and response.status_code in {200, 204}:
        return {}
    if response.status_code != 200:
        raise NativeAPIError(
            "remote_error", "Supervisely API returned an unsuccessful response."
        )
    if len(response.content) > 3_000_000:
        raise NativeAPIError("oversize", "Supervisely response exceeded the safe limit.")
    try:
        data = response.json()
    except ValueError as exc:
        raise NativeAPIError("bad_response", "Invalid Supervisely JSON response.") from exc
    if not isinstance(data, dict):
        raise NativeAPIError("bad_response", "Unexpected Supervisely response shape.")
    return data


def _identifier(info: dict) -> int:
    value = info.get("id")
    if type(value) is not int or value <= 0:
        raise NativeAPIError("bad_response", "Supervisely returned an invalid identifier.")
    return value


def _read_members(client: httpx.Client, base: str, token: str,
                  team_id: int) -> tuple[NativeMember, ...]:
    members: list[NativeMember] = []
    seen: set[int] = set()
    page = 1
    expected_total: int | None = None
    while True:
        result = _request_json(
            client, base, token, "members.list",
            {"teamId": team_id, "filter": [], "page": page, "per_page": 100},
        )
        entities = result.get("entities")
        total = result.get("total")
        page_count = result.get("pagesCount")
        if (not isinstance(entities, list) or type(total) is not int
                or type(page_count) is not int or not 0 <= total <= 10000
                or not 0 <= page_count <= 100):
            raise NativeAPIError("bad_response", "Unexpected Supervisely membership page.")
        if expected_total is None:
            expected_total = total
        if total != expected_total or page > max(page_count, 1):
            raise NativeAPIError(
                "members_changed", "Supervisely membership changed during reading; retry."
            )
        for item in entities:
            if not isinstance(item, dict):
                raise NativeAPIError("bad_response", "Invalid Supervisely member.")
            uid = _identifier(item)
            login = item.get("login")
            if (uid in seen or not isinstance(login, str) or not login.strip()
                    or len(login) > 320):
                raise NativeAPIError("bad_response", "Duplicate or invalid member identity.")
            raw_email = item.get("email")
            if raw_email is not None and (
                not isinstance(raw_email, str) or len(raw_email) > 320
            ):
                raise NativeAPIError("bad_response", "Invalid native member email.")
            role = item.get("role")
            if role is not None and not isinstance(role, str):
                raise NativeAPIError("bad_response", "Invalid native member role.")
            seen.add(uid)
            members.append(NativeMember(
                id=uid, login=login.strip(),
                email=(raw_email.strip().lower() or None) if raw_email else None,
                role=(role or "unknown").lower(),
            ))
        if page >= page_count:
            break
        page += 1
    if expected_total != len(members):
        raise NativeAPIError(
            "members_changed",
            "Supervisely membership count changed during pagination; retry.",
        )
    return tuple(members)


def read_team_snapshot(config: SuperviselyConfig, *, personal_token: str | None = None) -> NativeTeamSnapshot:
    if personal_token is None:
        base, token = require_connection(config)
    else:
        # Reuse all host checks without requiring that a personal credential
        # be stored or substituted for the organiser's integration token.
        url = urlsplit(config.server_url)
        allowed = {
            host.strip().lower() for host in get_settings().supervisely_allowed_hosts.split(",")
            if host.strip()
        }
        if (not config.team_id or url.scheme != "https" or not url.hostname
                or url.hostname.lower() not in allowed or url.port not in (None, 443)
                or url.path or url.query or url.fragment or url.username or url.password):
            raise NativeAPIError("untrusted_host", "Supervisely team host is not configured securely.")
        if not personal_token.strip():
            raise NativeAPIError("missing_token", "Enter your Supervisely API token.")
        base, token = config.server_url.rstrip("/"), personal_token.strip()
    # No redirects; do not forward tokens to alternate hosts.
    with httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False, trust_env=False,
    ) as client:
        user = _request_json(client, base, token, "users.me", {})
        team = _request_json(
            client, base, token, "teams.info", {"id": config.team_id}
        )
        if _identifier(team) != config.team_id:
            raise NativeAPIError("wrong_team", "Supervisely responded with another team.")
        workspace_id = config.workspace_id
        if workspace_id:
            workspace = _request_json(
                client, base, token, "workspaces.info", {"id": workspace_id}
            )
            if (_identifier(workspace) != workspace_id
                    or workspace.get("teamId") != config.team_id):
                raise NativeAPIError(
                    "wrong_workspace",
                    "Configured workspace does not belong to the selected team.",
                )
        name = team.get("name")
        login = user.get("login")
        if not isinstance(name, str) or not isinstance(login, str):
            raise NativeAPIError("bad_response", "Supervisely identity information incomplete.")
        members = _read_members(client, base, token, config.team_id)
    return NativeTeamSnapshot(
        team_id=config.team_id, team_name=name,
        workspace_id=workspace_id, account_id=_identifier(user),
        account_login=login, members=members,
    )


def _read_member_by_id(
    client: httpx.Client, base: str, token: str, team_id: int, user_id: int,
) -> NativeMember | None:
    """Use Supervisely SDK's targeted members.list filter instead of full roster.

    UserApi.get_member_info_by_id uses this exact filter, which keeps role
    updates responsive even when the team contains hundreds of members.
    """
    result = _request_json(client, base, token, "members.list", {
        "teamId": team_id, "filter": [{"field": "id", "operator": "=", "value": user_id}],
        "page": 1, "per_page": 20,
    })
    entities = result.get("entities")
    if not isinstance(entities, list) or len(entities) > 1:
        raise NativeAPIError("bad_response", "Supervisely member lookup was ambiguous.")
    if not entities:
        return None
    raw = entities[0]
    if not isinstance(raw, dict) or _identifier(raw) != user_id:
        raise NativeAPIError("bad_response", "Supervisely returned a mismatched member.")
    login = raw.get("login")
    role = raw.get("role")
    email = raw.get("email")
    if (not isinstance(login, str) or not login.strip()
            or not isinstance(role, str) or not role.strip()
            or (email is not None and not isinstance(email, str))):
        raise NativeAPIError("bad_response", "Supervisely returned an invalid member.")
    return NativeMember(
        id=user_id, login=login.strip(), role=role.lower(),
        email=(email.strip().lower() or None) if email else None,
    )


def change_member_role(
    config: SuperviselyConfig,
    *,
    user_id: int,
    target_role: str,
    expected_role: str | None = None,
) -> NativeMember:
    """Change a verified team member's role using the official members.editInfo.

    The role ID comes from roles.list, not from a guessed static mapping.
    Never downgrade the API credential's own admin account or any native
    admin. On failed or unverified writes the caller must retain local state.
    """
    if target_role not in {"manager", "annotator"}:
        raise NativeAPIError("invalid_role", "Only manager and annotator assignments are supported.")
    if type(user_id) is not int or user_id <= 0:
        raise NativeAPIError("invalid_user", "A verified Supervisely member ID is required.")
    base, token = require_connection(config)
    # Six short native requests are needed (actor, target, role list,
    # mutation and readback). Bound each request so a stalled upstream does
    # not strand the browser behind a long-lived Cloudflare tunnel request.
    with httpx.Client(
        timeout=httpx.Timeout(4.0, connect=2.0),
        follow_redirects=False, trust_env=False,
    ) as client:
        account = _request_json(client, base, token, "users.me", {})
        acting_id = _identifier(account)
        actor = _read_member_by_id(client, base, token, config.team_id, acting_id)
        target = _read_member_by_id(client, base, token, config.team_id, user_id)
        if actor is None or actor.role != "admin":
            raise NativeAPIError(
                "admin_required",
                "The shared Supervisely credential must belong to a team admin to change roles.",
            )
        if target is None:
            raise NativeAPIError("member_missing", "The person is not a member of the configured Supervisely team.")
        if target.id == acting_id or target.role == "admin":
            raise NativeAPIError("admin_protected", "Supervisely admin accounts cannot be demoted by Nightjar.")
        if expected_role is not None and target.role != expected_role:
            raise NativeAPIError(
                "stale_role", "Supervisely role changed since the last refresh. Refresh membership before editing.",
            )
        if target.role == target_role:
            return target
        roles = _request_json(client, base, token, "roles.list", {"filter": []})
        entries = roles.get("entities")
        if not isinstance(entries, list):
            raise NativeAPIError("bad_response", "Could not read Supervisely role definitions.")
        matches = [
            entry["id"] for entry in entries
            if isinstance(entry, dict) and entry.get("role") == target_role
            and type(entry.get("id")) is int and entry["id"] > 0
        ]
        if len(matches) != 1:
            raise NativeAPIError(
                "role_unavailable",
                "The requested role is not uniquely defined on this Supervisely instance.",
            )
        _request_json(client, base, token, "members.editInfo", {
            "id": target.id, "teamId": config.team_id, "roleId": matches[0],
        })
        verified = _read_member_by_id(client, base, token, config.team_id, target.id)
        if verified is None or verified.role != target_role:
            raise NativeAPIError(
                "role_unverified",
                "Supervisely did not confirm the new role. Refresh the team before retrying.",
            )
        return verified
