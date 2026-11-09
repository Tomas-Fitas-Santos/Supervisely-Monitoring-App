"""Read-only Supervisely invitation diagnosis using the existing organiser connection.

No participant token, and no native members.add or role mutation. Prefer the
app's encrypted local organiser connection; only prompt for that organiser's
token when the app has no saved connection.
"""
from __future__ import annotations

import argparse
import getpass

import supervisely as sly


def _connect(server: str | None):
    from monitoring.connection import LocalConnection
    from monitoring.db import database
    from monitoring.settings import Settings

    settings = Settings.load()
    api = None
    engine = None
    try:
        if settings.local:
            engine, sessions = database(settings.database_url)
            api = LocalConnection(sessions, settings.home).api()
    except Exception as exc:
        print("Saved organiser connection unavailable:", type(exc).__name__)
    finally:
        if engine is not None:
            engine.dispose()
    if api is not None:
        if server and api.server_address.rstrip("/") != server.rstrip("/"):
            raise SystemExit(
                "The saved organiser connection belongs to a different server. "
                "Use the configured server or explicitly sign in to the app again."
            )
        print("Using the organiser connection already saved in the Monitoring App.")
        return api

    address = (server or settings.server_address).rstrip("/")
    print("No saved organiser connection found. Use YOUR organiser/admin token.")
    token = getpass.getpass("Organiser API token (hidden): ").strip()
    if not token:
        raise SystemExit("No token supplied. No request performed.")
    return sly.Api(address, token)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect existing Supervisely membership; never invite or change roles."
    )
    parser.add_argument("--email", required=True, help="Exact registered email to check")
    parser.add_argument("--team-id", type=int, required=True, help="Existing team ID")
    parser.add_argument("--server", default=None, help="Optional Supervisely server URL")
    args = parser.parse_args()
    email = args.email.strip().casefold()
    if email.count("@") != 1 or args.team_id <= 0:
        parser.error("Supply a registered email and positive Supervisely team ID.")

    api = _connect(args.server)
    try:
        me = api.user.get_my_info()
    except Exception as exc:
        print("Authentication failed:", type(exc).__name__)
        return 2

    print("Supervisely server:", api.server_address.rstrip("/"))
    print("Organiser:", me.login, "(user ID:", me.id, ")")
    try:
        membership = api.user.get_member_info_by_id(args.team_id, me.id)
    except Exception as exc:
        print("Cannot check organiser team membership:", type(exc).__name__)
        return 2
    if membership is None:
        print("The authenticated account is not in the configured team.")
        return 2
    print("Team role:", membership.role)
    if str(membership.role).lower() != "admin":
        print("This account lacks the team's Admin invitation role.")

    try:
        members = api.user.get_team_members(args.team_id)
    except Exception as exc:
        print("Cannot read this team's member list:", type(exc).__name__)
        return 2

    matches = [
        m for m in members
        if str(getattr(m, "email", "") or "").strip().casefold() == email
    ]
    if matches:
        for m in matches:
            print("Already visible in team:", getattr(m, "login", "<unknown>"),
                  "(user ID:", getattr(m, "id", None), ")")
        print("Use 'Refresh participants & monitors' in the Monitoring App.")
    else:
        print("No existing team member exposes that exact email.")
        print("This does NOT mean the email is invalid or the account is absent.")
        print("A team-scoped directory cannot enumerate users outside the team.")
        print("Do not re-send a failed invitation without identifying the API error.")

    print("Global users.list was deliberately NOT called: its endpoint returned")
    print("HTTP 404 Page not found on this Community/Pro server.")
    print("The invite operation members.add was NOT called either.")
    print("If the Supervisely web UI can invite by email, use its Team Members")
    print("screen and then refresh the Monitoring App. The actual browser invite")
    print("request (method, URL path, and payload field names only) can be")
    print("inspected in browser Developer Tools without exposing any token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
