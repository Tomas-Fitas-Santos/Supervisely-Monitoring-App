"""Safe local operator provisioning. No default accounts, no passwords in shell history."""
import argparse
import getpass

from sqlalchemy import select

from .auth import ROLES, normalized_email
from .db import session_factory
from .models import StaffAccount
from .security import password_hash


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage real local Annotatiathon staff accounts")
    cmd = parser.add_subparsers(dest="action", required=True)
    new = cmd.add_parser("create-staff", help="Create one authorised staff account")
    new.add_argument("--email", required=True)
    new.add_argument("--name", required=True)
    new.add_argument("--roles", required=True, help="Comma-separated app roles")
    args = parser.parse_args()
    roles = sorted({r.strip().lower() for r in args.roles.split(",") if r.strip()})
    if not roles or not set(roles).issubset(ROLES):
        parser.error("Roles must be a comma-separated subset of: " + ", ".join(sorted(ROLES)))
    try:
        email = normalized_email(args.email)
    except ValueError as exc:
        parser.error(str(exc))
    password = getpass.getpass("New staff password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords did not match.")
    try:
        hashed = password_hash(password)
    except ValueError as exc:
        parser.error(str(exc))
    with session_factory().begin() as db:
        if db.scalar(select(StaffAccount).where(StaffAccount.email == email)):
            parser.error("This staff email already exists.")
        db.add(StaffAccount(email=email, name=args.name.strip(), password_hash=hashed,
                            roles=roles))
    print("Staff account created successfully (password not displayed).")


if __name__ == "__main__":
    main()
