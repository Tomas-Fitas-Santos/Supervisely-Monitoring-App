"""Safe local operator provisioning. No default accounts, no passwords in shell history."""
import argparse
import getpass

from sqlalchemy import delete, select

from .auth import ROLES, normalized_email
from .db import session_factory
from .models import AuditEvent, StaffAccount, StaffSession
from .security import password_hash


def prompted_password() -> str:
    password = getpass.getpass("New staff password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords did not match.")
    return password_hash(password)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage real local Annotatiathon staff accounts")
    cmd = parser.add_subparsers(dest="action", required=True)
    new = cmd.add_parser("create-staff", help="Create one authorised staff account")
    new.add_argument("--email", required=True)
    new.add_argument("--name", required=True)
    new.add_argument("--roles", required=True, help="Comma-separated app roles")
    reset = cmd.add_parser(
        "reset-password",
        help="Securely reset an existing staff password and revoke sessions",
    )
    reset.add_argument("--email", required=True)
    backfill = cmd.add_parser("backfill-snapshots", help="Cache existing snapshot counts for dashboard SQL")
    backfill.add_argument("--event-id")
    args = parser.parse_args()
    if args.action == "backfill-snapshots":
        from .snapshot_metrics import backfill_snapshot_counts
        with session_factory()() as db:
            print(backfill_snapshot_counts(db, args.event_id))
        return
    try:
        email = normalized_email(args.email)
    except ValueError as exc:
        parser.error(str(exc))

    if args.action == "reset-password":
        with session_factory().begin() as db:
            staff = db.scalar(
                select(StaffAccount).where(StaffAccount.email == email).with_for_update()
            )
            if staff is None:
                parser.error("No staff account exists for this email.")
            try:
                hashed = prompted_password()
            except ValueError as exc:
                parser.error(str(exc))
            staff.password_hash = hashed
            staff.login_failures = 0
            staff.locked_until = None
            db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
            db.add(AuditEvent(
                action="staff.password.reset_cli",
                target_type="staff", target_id=staff.id,
                details={"method": "local_operator_cli", "sessions_revoked": True},
            ))
        print("Staff password updated and existing sessions revoked.")
        return

    roles = sorted({r.strip().lower() for r in args.roles.split(",") if r.strip()})
    if not roles or not set(roles).issubset(ROLES):
        parser.error("Roles must be a comma-separated subset of: " + ", ".join(sorted(ROLES)))
    try:
        hashed = prompted_password()
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
