"""Safe local operator provisioning. No default accounts, no passwords in shell history."""
import argparse
import getpass

from sqlalchemy import delete, select

from .auth import ROLES, normalized_email
from .db import session_factory
from .models import ApprovedStaffEmail, AuditEvent, StaffAccount, StaffSession, epoch
from .organiser_access import access_roles, configured_emails, is_restricted_mode
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
    cmd.add_parser("sync-organiser-access", help="Bootstrap PostgreSQL approvals once and reconcile staff")
    approval = cmd.add_parser("approve-email", help="Approve a staff email in PostgreSQL")
    approval.add_argument("--email", required=True)
    approval.add_argument("--role", choices=("admin", "organiser"), required=True)
    reset = cmd.add_parser(
        "reset-password",
        help="Securely reset an existing staff password and revoke sessions",
    )
    reset.add_argument("--email", required=True)
    backfill = cmd.add_parser("backfill-snapshots", help="Cache existing snapshot counts for dashboard SQL")
    backfill.add_argument("--event-id")
    review_backfill = cmd.add_parser(
        "backfill-frame-reviews",
        help="Queue all saved submitted-frame JSON from existing jobs, without contacting Supervisely",
    )
    review_backfill.add_argument("--event-id")
    args = parser.parse_args()
    if args.action == "backfill-snapshots":
        from .snapshot_metrics import backfill_snapshot_counts
        with session_factory()() as db:
            print(backfill_snapshot_counts(db, args.event_id))
        return
    if args.action == "backfill-frame-reviews":
        from .review_backfill import backfill_frame_reviews

        print(backfill_frame_reviews(session_factory(), event_id=args.event_id))
        return
    if args.action == "approve-email":
        try:
            email = normalized_email(args.email)
        except ValueError as exc:
            parser.error(str(exc))
        with session_factory().begin() as db:
            entry = db.get(ApprovedStaffEmail, email)
            if entry is None:
                db.add(ApprovedStaffEmail(email=email, role=args.role,
                                          enabled=True, created_at=epoch()))
            else:
                entry.role = args.role
                entry.enabled = True
        print("Approved:", email, "as", args.role)
        print("Run sync-organiser-access to reconcile an existing login.")
        return
    if args.action == "sync-organiser-access":
        updated, missing, disabled = [], [], []
        with session_factory().begin() as db:
            # Only an empty database allowlist may be initialized from .env.live.
            # After initialization, PostgreSQL alone governs all authorisation.
            approvals = db.scalars(select(ApprovedStaffEmail).with_for_update()).all()
            if not approvals:
                bootstrap = configured_emails()
                if not bootstrap or not any("admin" in roles for roles in bootstrap.values()):
                    parser.error("No approved emails in PostgreSQL. Configure initial private "
                                 "NIGHTJAR_ADMIN_EMAILS and NIGHTJAR_ORGANISER_EMAILS.")
                for address, roles in bootstrap.items():
                    db.add(ApprovedStaffEmail(
                        email=address, role="admin" if "admin" in roles else "organiser",
                        enabled=True, created_at=epoch(),
                    ))
                db.flush()
                approvals = db.scalars(select(ApprovedStaffEmail)).all()
                print("Bootstrapped PostgreSQL allowlist:", len(approvals))
            approved = {
                a.email: (["admin", "organiser"] if a.role == "admin" else ["organiser"])
                for a in approvals if a.enabled
            }
            if not any("admin" in roles for roles in approved.values()):
                parser.error("At least one enabled Admin is required in the approved database.")
            accounts = db.scalars(select(StaffAccount).with_for_update()).all()
            by_email = {a.email: a for a in accounts}
            for address, roles in approved.items():
                staff = by_email.get(address)
                if staff is None:
                    missing.append(address)
                    continue
                changed = staff.roles != roles or not staff.active
                staff.roles = roles
                staff.active = True
                staff.login_failures = 0
                staff.locked_until = None
                if changed:
                    db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
                    db.add(AuditEvent(
                        action="staff.access.database_sync", target_type="staff",
                        target_id=staff.id, details={"roles": roles, "enabled": True},
                    ))
                    updated.append(address)
            if is_restricted_mode():
                for staff in accounts:
                    if staff.email not in approved and staff.active:
                        staff.active = False
                        db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
                        db.add(AuditEvent(
                            action="staff.access.database_sync", target_type="staff",
                            target_id=staff.id, details={
                                "enabled": False, "reason": "not_database_approved",
                            },
                        ))
                        disabled.append(staff.email)
        print("Enabled/updated:", len(updated), "Can self-register:", len(missing),
              "Other staff disabled:", len(disabled))
        for address in missing:
            print("Not registered:", address)
        return
    try:
        email = normalized_email(args.email)
    except ValueError as exc:
        parser.error(str(exc))

    if is_restricted_mode():
        with session_factory()() as db:
            if not access_roles(db, email):
                parser.error("Email is not in the approved staff database.")
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
