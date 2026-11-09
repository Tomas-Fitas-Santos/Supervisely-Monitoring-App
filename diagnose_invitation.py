"""Read-only diagnosis for one Supervisely email invitation.

This script NEVER calls members.add or changes any team. Supply the API token
at an interactive prompt: it is not saved or printed.
"""
from __future__ import annotations

import argparse
import getpass

import supervisely as sly


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a registered Supervisely account without inviting it."
    )
    parser.add_argument("--email", required=True, help="Exact registered email")
    parser.add_argument("--team-id", type=int, required=True, help="Existing Supervisely team")
    parser.add_argument(
        "--server", default="https://app.supervisely.com",
        help="Supervisely server address (default: community cloud)",
    )
    args = parser.parse_args()
    email = args.email.strip().casefold()
    if email.count("@") != 1 or args.team_id <= 0:
        parser.error("Supply an existing team ID and the target registered email.")
    token = getpass.getpass("Supervisely API token (hidden): ").strip()
    if not token:
        parser.error("A token is required for read-only API checks.")
    api = sly.Api(args.server.rstrip("/"), token)
    print("Supervisely server:", args.server.rstrip("/"))
    try:
        me = api.user.get_my_info()
    except Exception as exc:
        print("AUTH/API FAILED:", type(exc).__name__)
        print("Verify SERVER_ADDRESS, SDK compatibility and the actual API token.")
        return 2
    print("Authenticated as:", me.login, "(user ID:", me.id, ")")
    try:
        membership = api.user.get_member_info_by_id(args.team_id, me.id)
        print("Team admin check:", getattr(membership, "role", None)
              if membership else "not a member")
    except Exception as exc:
        print("Team member lookup failed:", type(exc).__name__)
    try:
        members = api.user.get_team_members(args.team_id)
        matches = [
            member for member in members
            if str(getattr(member, "email", "") or "").strip().casefold() == email
        ]
        print("Existing team member with exact email:",
              ", ".join(str(m.login) for m in matches) if matches else "not visible")
    except Exception as exc:
        print("Team member directory lookup unavailable:", type(exc).__name__)
    try:
        users = api.user.get_list()
    except Exception as exc:
        print("Instance-wide users.list is not accessible:", type(exc).__name__)
        print("Email-to-username lookup is unavailable with this token.")
    else:
        matches = [
            user for user in users
            if str(getattr(user, "email", "") or "").strip().casefold() == email
        ]
        if len(matches) == 1:
            user = matches[0]
            print("Exact registered email resolves to username:", user.login)
            print("Account disabled:", bool(getattr(user, "disabled", False)))
        elif len(matches) > 1:
            print("AMBIGUOUS: multiple accounts have this email. Do not invite.")
        else:
            print("Email is not visible in the accessible global directory.")
            print("This does not prove the account is absent.")
    print("NO invitation was sent; no native team role was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
