"""Event competition-pair roster, balanced staff assignments and arrival check-in.

Pair IDs are application registration IDs. A native Supervisely team ID is never
used as the identity of an event pair in this module. The module deliberately
does not provision native jobs or change native reviewer permissions.
"""
import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import select

from .db import (
    ArrivalCheckin, Audit, EventConfig, EventMember, Registration,
    SetupOperation, Team, TeamOnboarding, now,
)
from .service import WorkflowError


class EventRoster:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def _demo_dual_role():
        return (
            os.getenv("MONITORING_LIVE_DEMO") == "1"
            or os.getenv("MONITORING_PILOT") == "1"
        ) and os.getenv("MONITORING_SIMULATOR") != "1"

    @staticmethod
    def _people(row):
        people = row.members or []
        return [p for p in people if p.get("email")]

    @staticmethod
    def _assigned_id(session, registration):
        if registration.team_id:
            native = session.get(Team, registration.team_id)
            if native and native.monitor_id:
                return int(native.monitor_id)
        return int(registration.monitor_id or 0)

    @staticmethod
    def _revision(session):
        """Stable fingerprint of the state used by an assignment preview."""
        regs = []
        for row in session.scalars(select(Registration).order_by(Registration.id)):
            regs.append({
                "id": row.id,
                "monitor_id": row.monitor_id,
                "team_id": row.team_id,
                "members": [p.get("email", "").strip().casefold() for p in row.members or []],
            })
        staff = [(m.user_id, m.group) for m in session.scalars(
            select(EventMember).order_by(EventMember.user_id))]
        encoded = json.dumps({"registrations": regs, "staff": staff},
                             sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _named_monitors(session):
        monitors = {int(m.user_id): {
            "id": int(m.user_id), "name": m.name, "login": m.login,
            "demo_dual_role": False,
        } for m in session.scalars(
            select(EventMember).where(EventMember.group == "monitors")
        )}
        config = session.get(EventConfig, 1)
        if config and config.owner_id:
            monitors.setdefault(int(config.owner_id), {
                "id": int(config.owner_id),
                "name": config.owner_login or "Organiser",
                "login": config.owner_login or "",
                "demo_dual_role": False,
            })
        if EventRoster._demo_dual_role():
            # DEMO can put its two real annotators on the monitoring roster.
            # This does not grant them native Supervisely permissions.
            for reg in session.scalars(select(Registration)):
                for person in EventRoster._people(reg):
                    if person.get("user_id") and person.get("status") in ("accepted", "verified", "matched"):
                        pid = int(person["user_id"])
                        monitors.setdefault(pid, {
                            "id": pid, "name": person.get("name") or person["email"],
                            "login": person.get("login") or person["email"],
                            "demo_dual_role": True,
                        })
        return monitors

    def overview(self, actor, organiser=False):
        with self.sessions() as session:
            monitors = self._named_monitors(session)
            checkins = {
                (row.registration_id, row.member_email): row
                for row in session.scalars(select(ArrivalCheckin))
            }
            teams, present, total = [], 0, 0
            for reg in session.scalars(select(Registration).order_by(Registration.team_name)):
                assigned = self._assigned_id(session, reg)
                members = self._people(reg)
                demo_member = self._demo_dual_role() and any(
                    int(m.get("user_id") or 0) == int(actor) for m in members
                )
                if not organiser and assigned != actor and not demo_member:
                    continue
                people = []
                for member in members:
                    email = member["email"].strip().casefold()
                    attendance = checkins.get((reg.id, email))
                    checked = bool(attendance and attendance.present)
                    people.append({
                        "email": email, "name": member.get("name") or email,
                        "user_id": member.get("user_id"),
                        "checked_in": checked,
                        "checked_in_at": attendance.checked_in_at if checked else None,
                        "checked_in_by": attendance.actor_id if checked else None,
                    })
                    present += int(checked)
                    total += 1
                count = sum(p["checked_in"] for p in people)
                teams.append({
                    "id": reg.id, "name": reg.team_name,
                    "monitor_id": assigned,
                    "monitor_name": monitors.get(assigned, {}).get("name", "Unassigned"),
                    "native_provisioned": bool(reg.team_id),
                    "members": people,
                    "present": count,
                    "total": len(people),
                    "arrival_status": (
                        "complete" if people and count == len(people)
                        else "partial" if count else "not_arrived"
                    ),
                })
            return {
                "teams": teams,
                "monitors": [monitors[mid] for mid in sorted(monitors)]
                    if organiser else [],
                "summary": {
                    "teams": len(teams),
                    "participants": total,
                    "checked_in": present,
                    "missing": total - present,
                    "complete_teams": sum(t["arrival_status"] == "complete" for t in teams),
                    "partial_teams": sum(t["arrival_status"] == "partial" for t in teams),
                },
            }

    def set_checkin(self, actor, registration_id, email, present, note="", organiser=False):
        if not isinstance(present, bool):
            raise WorkflowError("Invalid check-in state.")
        email = str(email or "").strip().casefold()
        note = str(note or "").strip()
        if len(note) > 500:
            raise WorkflowError("Keep the check-in note under 500 characters.")
        with self.sessions.begin() as session:
            reg = session.get(Registration, str(registration_id or ""))
            if not reg:
                raise WorkflowError("The competition team no longer exists.")
            members = self._people(reg)
            if email not in {p["email"].strip().casefold() for p in members}:
                raise WorkflowError("Participant is not registered for this team.")
            demo_member = self._demo_dual_role() and any(
                int(m.get("user_id") or 0) == actor for m in members
            )
            if not organiser and self._assigned_id(session, reg) != actor and not demo_member:
                raise WorkflowError("Only the assigned monitor may check in this team.")
            record = session.get(ArrivalCheckin, (reg.id, email))
            if record and record.present == present:
                return False  # Idempotent double-click; no false duplicate audit.
            if not present and not note:
                raise WorkflowError("Provide a reason to undo an arrival check-in.")
            if record is None:
                record = ArrivalCheckin(registration_id=reg.id, member_email=email)
                session.add(record)
            record.present = present
            record.checked_in_at = now() if present else None
            record.actor_id = int(actor)
            record.updated_at = now()
            record.note = note
            session.add(Audit(
                team_id=reg.team_id or 0, batch_id="", actor_id=int(actor),
                action="participant_checkin" if present else "participant_checkin_undo",
                details={"registration_id": reg.id, "member_email": email, "note": note,
                         "present": present},
            ))
        return True

    def _validate_monitors(self, session, monitor_ids):
        if not isinstance(monitor_ids, list) or not monitor_ids:
            raise WorkflowError("Select at least one registered event monitor.")
        if any(isinstance(value, bool) or str(value).strip() != str(int(value))
               for value in monitor_ids):
            raise WorkflowError("Invalid monitor identifier.")
        selected = [int(i) for i in monitor_ids]
        if len(set(selected)) != len(selected):
            raise WorkflowError("Select each monitor once.")
        available = self._named_monitors(session)
        if not set(selected) <= set(available):
            raise WorkflowError("Selected monitors are no longer on the roster.")
        return sorted(selected)

    def preview_balance(self, actor, monitor_ids, rebalance=True):
        """Balance logical competition pairs, preserving all provisioned teams.

        This has no remote effects. Only unprovisioned registrations can move.
        """
        if not isinstance(rebalance, bool):
            raise WorkflowError("Invalid rebalance option.")
        with self.sessions.begin() as session:
            selected = self._validate_monitors(session, monitor_ids)
            registrations = list(session.scalars(
                select(Registration).order_by(Registration.team_name, Registration.id)
            ))
            loads = {mid: 0 for mid in selected}
            movable = []
            issues = []
            for reg in registrations:
                assigned = self._assigned_id(session, reg)
                members = self._people(reg)
                if len(members) != 2 or members[0]["email"].casefold() == members[1]["email"].casefold():
                    issues.append(f"{reg.team_name}: two distinct participants are required")
                    continue
                if reg.team_id or (assigned and (not rebalance or assigned not in selected)):
                    if assigned in loads:
                        loads[assigned] += 1
                else:
                    movable.append(reg)
            rows = []
            for reg in movable:
                mid = min(selected, key=lambda uid: (loads[uid], uid))
                loads[mid] += 1
                if int(reg.monitor_id or 0) != mid:
                    rows.append({"registration_id": reg.id, "team_name": reg.team_name,
                                 "from_monitor_id": int(reg.monitor_id or 0),
                                 "monitor_id": mid})
            if not rows:
                return None, {"rows": [], "loads": loads, "issues": issues}
            key = uuid4().hex
            session.add(SetupOperation(
                id=key, actor_id=int(actor), kind="roster_balance", state="planned",
                details={"rows": rows, "selected": selected, "rebalance": rebalance,
                         "roster_revision": self._revision(session)},
                result={},
            ))
            return key, {"rows": rows, "loads": loads, "issues": issues}

    def apply_balance(self, actor, plan_id, monitor_emails):
        """Atomic commit for not-yet-provisioned pairs only.

        The caller supplies native-verified monitor emails to keep future
        invitation/provision operations consistent with the staff assignment.
        """
        with self.sessions.begin() as session:
            op = session.get(SetupOperation, str(plan_id or ""))
            if not op or op.kind != "roster_balance" or op.state != "planned":
                raise WorkflowError("Select an unapplied balanced assignment preview.")
            self._validate_monitors(session, op.details["selected"])
            if self._revision(session) != op.details["roster_revision"]:
                raise WorkflowError("Roster changed. Preview balanced assignments again.")
            rows = op.details["rows"]
            for item in rows:
                reg = session.get(Registration, item["registration_id"])
                if reg.team_id is not None or int(reg.monitor_id or 0) != item["from_monitor_id"]:
                    raise WorkflowError("A team was provisioned or reassigned. Preview again.")
                email = str(monitor_emails.get(item["monitor_id"]) or "").strip().casefold()
                if not email or email.count("@") != 1:
                    raise WorkflowError("Verify the selected monitor email in Supervisely before applying.")
            for item in rows:
                reg = session.get(Registration, item["registration_id"])
                reg.monitor_id = item["monitor_id"]
                onboarding = session.get(TeamOnboarding, reg.id)
                if onboarding:
                    onboarding.manager_email = monitor_emails[item["monitor_id"]].strip().casefold()
                session.add(Audit(
                    team_id=0, batch_id="", actor_id=int(actor), action="roster_monitor_assigned",
                    details={"registration_id": reg.id, "previous_monitor_id": item["from_monitor_id"],
                             "monitor_id": item["monitor_id"], "operation_id": op.id},
                ))
            op.state = "completed"
            op.result = {"assigned": len(rows)}
        return len(rows)

    def assign_one(self, actor, registration_id, monitor_id, monitor_email):
        """Manual assignment before native team provisioning."""
        with self.sessions.begin() as session:
            selected = self._validate_monitors(session, [monitor_id])
            reg = session.get(Registration, str(registration_id or ""))
            if not reg:
                raise WorkflowError("Competition team not found.")
            if reg.team_id is not None:
                raise WorkflowError(
                    "This team is already provisioned in Supervisely. Use native monitor "
                    "handover rather than changing only the event roster."
                )
            if len(self._people(reg)) != 2:
                raise WorkflowError("Competition team requires exactly two participants.")
            mid = selected[0]
            old = int(reg.monitor_id or 0)
            if old == mid:
                return False
            if not monitor_email or monitor_email.count("@") != 1:
                raise WorkflowError("Verify this monitor's email in Supervisely.")
            reg.monitor_id = mid
            plan = session.get(TeamOnboarding, reg.id)
            if plan:
                plan.manager_email = monitor_email.strip().casefold()
            session.add(Audit(
                team_id=0, batch_id="", actor_id=int(actor), action="roster_monitor_assigned",
                details={"registration_id": reg.id, "previous_monitor_id": old,
                         "monitor_id": mid, "manual": True},
            ))
        return True
