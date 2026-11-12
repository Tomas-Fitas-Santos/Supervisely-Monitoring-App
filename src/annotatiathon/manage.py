"""Safe local operator provisioning. No default accounts, no passwords in shell history."""
import argparse
import getpass

from sqlalchemy import delete, func, select

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
    rename = cmd.add_parser(
        "change-staff-email",
        help="Correct an existing staff email and matching approval without changing the password",
    )
    rename.add_argument("--from-email", required=True)
    rename.add_argument("--to-email", required=True)
    resolve = cmd.add_parser(
        "keep-existing-staff-account",
        help="Retire an obsolete approved email when the corrected staff account already exists",
    )
    resolve.add_argument("--obsolete-email", required=True)
    resolve.add_argument("--keep-email", required=True)
    resolve.add_argument(
        "--reconcile-kept-access", action="store_true",
        help="Explicitly activate the corrected existing Organiser account and reconcile its role "
             "with the approved staff role; never automatically grant Admin",
    )
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
    if args.action == "keep-existing-staff-account":
        try:
            obsolete_email = normalized_email(args.obsolete_email)
            keep_email = normalized_email(args.keep_email)
        except ValueError as exc:
            parser.error(str(exc))
        if obsolete_email == keep_email:
            parser.error("Obsolete and kept email must be different.")
        with session_factory().begin() as db:
            approvals = list(db.scalars(select(ApprovedStaffEmail).where(
                ApprovedStaffEmail.email.in_([obsolete_email, keep_email]),
            ).with_for_update()))
            approval_by_email = {entry.email: entry for entry in approvals}
            old_approval = approval_by_email.get(obsolete_email)
            keep_approval = approval_by_email.get(keep_email)
            if old_approval is None or not old_approval.enabled:
                parser.error("Obsolete email does not have an enabled staff approval; no changes made.")
            if old_approval.role not in {"organiser", "admin"}:
                parser.error("Unexpected source approval role; no changes made.")
            if keep_approval is not None and (
                not keep_approval.enabled or keep_approval.role != old_approval.role
            ):
                parser.error("Corrected email approval conflicts with existing role/status; no changes made.")
            matches = list(db.scalars(select(StaffAccount).where(
                func.lower(StaffAccount.email).in_([obsolete_email, keep_email]),
            ).with_for_update()))
            previous = [
                staff for staff in matches if staff.email.strip().lower() == obsolete_email
            ]
            kept = [
                staff for staff in matches if staff.email.strip().lower() == keep_email
            ]
            if len(kept) != 1 or len(previous) > 1:
                parser.error("Cannot identify exactly one corrected account safely; no changes made.")
            target = kept[0]
            expected_roles = (
                ["admin", "organiser"] if old_approval.role == "admin" else ["organiser"]
            )
            must_reconcile = target.roles != expected_roles or not target.active
            if must_reconcile and not args.reconcile_kept_access:
                parser.error(
                    "Corrected account roles or active status do not match the approved role "
                    "(current roles: " + ", ".join(target.roles or []) +
                    "; active: " + str(target.active) + "). "
                    "To retain this account and explicitly give it the approved Organiser role, "
                    "rerun with --reconcile-kept-access. No changes made."
                )
            if must_reconcile and old_approval.role != "organiser":
                parser.error(
                    "Cannot automatically reconcile an Admin account. Review approvals first."
                )
            if "admin" in target.roles and old_approval.role != "admin":
                parser.error("Corrected account has Admin privilege; refusing to demote it.")
            if previous and "admin" in previous[0].roles and old_approval.role != "admin":
                parser.error("Obsolete account has Admin privilege; refusing to retire it.")
            if previous and previous[0].roles != expected_roles and not args.reconcile_kept_access:
                parser.error(
                    "Obsolete account has unexpected roles; rerun with "
                    "--reconcile-kept-access only after verifying the two emails."
                )
            kept_roles_before = list(target.roles)
            kept_active_before = target.active
            if must_reconcile:
                target.roles = expected_roles
                target.active = True
                target.login_failures = 0
                target.locked_until = None
                db.execute(delete(StaffSession).where(
                    StaffSession.staff_id == target.id,
                ))
            if keep_approval is None:
                db.add(ApprovedStaffEmail(
                    email=keep_email, role=old_approval.role,
                    enabled=True, created_at=epoch(),
                ))
            old_approval.enabled = False
            if previous:
                previous[0].active = False
                db.execute(delete(StaffSession).where(
                    StaffSession.staff_id == previous[0].id,
                ))
            db.add(AuditEvent(
                staff_id=target.id, action="staff.email.conflict.resolved_cli",
                target_type="staff", target_id=target.id,
                details={
                    "obsolete_email": obsolete_email,
                    "kept_email": keep_email,
                    "roles_preserved": not must_reconcile,
                    "approved_role_applied": must_reconcile,
                    "kept_prior_roles": kept_roles_before,
                    "kept_prior_active": kept_active_before,
                    "kept_sessions_revoked": must_reconcile,
                    "password_preserved": True,
                    "obsolete_account_disabled": bool(previous),
                    "obsolete_sessions_revoked": bool(previous),
                    "accounts_merged": False,
                },
            ))
        print("Corrected staff account kept:", keep_email)
        print("Obsolete email access disabled:", obsolete_email)
        if must_reconcile:
            print("Corrected account activated and role reconciled:", ", ".join(expected_roles))
            print("Corrected account sessions revoked; existing password and ID preserved.")
        else:
            print("Kept account ID, password and role unchanged.")
        print("accounts were not merged; obsolete account retained but disabled.")
        print("Use the corrected email and its existing Nightjar password to sign in.")
        return
    if args.action == "change-staff-email":
        try:
            old_email = normalized_email(args.from_email)
            new_email = normalized_email(args.to_email)
        except ValueError as exc:
            parser.error(str(exc))
        if old_email == new_email:
            parser.error("The old and corrected email must be different.")
        with session_factory().begin() as db:
            # Lock the persistent approval rows and account candidates.
            # Never infer ownership or silently merge two real staff accounts.
            approvals = list(db.scalars(select(ApprovedStaffEmail).where(
                ApprovedStaffEmail.email.in_([old_email, new_email]),
            ).with_for_update()))
            by_approval = {a.email: a for a in approvals}
            old_approval = by_approval.get(old_email)
            new_approval = by_approval.get(new_email)
            if old_approval is None or not old_approval.enabled:
                parser.error("Source email is not an enabled database-approved staff email.")
            matches = list(db.scalars(select(StaffAccount).where(
                func.lower(StaffAccount.email).in_([old_email, new_email]),
            ).with_for_update()))
            source = [staff for staff in matches
                      if staff.email.strip().lower() == old_email]
            destination = [staff for staff in matches
                           if staff.email.strip().lower() == new_email]
            if len(source) != 1:
                parser.error("Expected exactly one existing source account; no changes made.")
            if destination:
                parser.error("A staff account already exists under the corrected email; no accounts were merged.")
            if new_approval is not None:
                if not new_approval.enabled or new_approval.role != old_approval.role:
                    parser.error("The target has a conflicting approval; resolve it before changing the account.")
                # The correct address was already approved: retain its record
                # and remove only the incorrect allowlist entry.
                db.delete(old_approval)
            else:
                # Move the existing approval, preserving its role and creation date.
                old_approval.email = new_email
            staff = source[0]
            staff.email = new_email
            db.execute(delete(StaffSession).where(StaffSession.staff_id == staff.id))
            db.add(AuditEvent(
                staff_id=staff.id,
                action="staff.email.corrected_cli",
                target_type="staff", target_id=staff.id,
                details={
                    "old_email": old_email, "new_email": new_email,
                    "role": old_approval.role,
                    "password_preserved": True, "sessions_revoked": True,
                },
            ))
        print("Corrected Nightjar staff email:", old_email, "->", new_email)
        print("Existing account ID, password and role preserved; sessions revoked.")
        print("The organiser must now sign in using the corrected email.")
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
            by_email = {}
            for staff in accounts:
                by_email.setdefault(staff.email.strip().lower(), []).append(staff)
            duplicates = [address for address in approved
                          if len(by_email.get(address, [])) > 1]
            if duplicates:
                parser.error("Ambiguous staff accounts with duplicate email casing: "
                             + ", ".join(duplicates) + ". Reconcile safely before syncing.")
            for address, roles in approved.items():
                matches = by_email.get(address, [])
                staff = matches[0] if matches else None
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
                    if staff.email.strip().lower() not in approved and staff.active:
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
