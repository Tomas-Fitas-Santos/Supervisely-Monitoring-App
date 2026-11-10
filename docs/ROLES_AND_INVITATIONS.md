# Nightjar roster roles, Supervisely membership roles and invitations

This document describes live event behavior in the existing Nightjar
application. There is no demo/production behavioral fork.

## Role mapping

| Nightjar event role | Supervisely team role | Nightjar staff login |
| --- | --- | --- |
| Organiser and monitor (existing team owner) | **Admin** (never demoted) | Organiser |
| Monitor | **Manager** | Allowed when roster-approved; each monitor has their own Nightjar account |
| Participant | **Annotator** | Not allowed to self-register or use monitoring pages |

Nightjar event roles are independent of the more detailed operational
permissions inside the Nightjar app (organiser, monitor, reception, etc.).
The Supervisely role describes access to the **one configured Supervisely
team**, not access to other teams or workspaces. Supervisely's built-in roles
are not interchangeable; changes must use the native **team** membership,
never global user roles.

## Editing roles in People & Teams

1. Organiser edits an existing person in **People & Teams → Edit** and
   chooses Monitor or Participant.
2. If that person has a verified native Supervisely membership,
   the server calls the official `members.editInfo` API with the correct
   `roleId` discovered dynamically using `roles.list`.
3. Nightjar re-reads the live team membership and **verifies** that the
   person's native role matches the target before committing the local role.
   It refuses ambiguous/stale IDs and protects all Supervisely Admins.
4. If Supervisely fails, the Nightjar role remains unchanged, and the UI
   displays an error. If the remote write succeeded but verification failed,
   refresh membership and retry after reviewing both systems; do not blindly
   repeat uncertain remote writes.
5. Reclassifying a monitor to participant also disables that person's
   Nightjar monitoring login and revokes active sessions. Re-promoting
   can re-enable an account disabled by this role conversion.
6. If a person is **not yet invited to Supervisely**, the event role can be
   changed locally, and the organiser then invites them with the corresponding
   native role. If an invitation was **already reported sent or is pending**,
   Nightjar blocks the role change until the native invitation is reviewed or
   the member joins; it cannot reliably mutate a pending native invitation
   without a verified Supervisely member ID.
7. Existing joined member roles **cannot be changed through a bulk CSV import**.
   The import refuses those changes and directs the organiser to the individual
   Edit operation so remote failures cannot leave a partially applied batch.
8. A joined member whose role is unexpectedly inconsistent can be corrected
   using **Sync Supervisely role now** from the edit dialog. Never use it
   on an account you have not verified.

The organiser's own Supervisely Admin membership stays Admin, and that
person remains an organiser/monitor locally.

## Who may sign in to Nightjar?

- Organisers have a separately provisioned Nightjar organiser account.
- Monitors on the approved active-event roster may create a Nightjar account
  after verifying the **email they control** using an eight-digit code.
- Participants cannot self-register as monitors.
- When a person's event role changes to Participant or the record is
  deactivated, their corresponding Nightjar monitor account is disabled and
  sessions revoked. For joined monitors with a verified native role, the
  Nightjar authorization layer also requires Manager status.
- These checks do not automatically create Supervisely memberships or
  grant permissions on other teams. Supervisely itself applies its own
  role permissions to annotation jobs.

SMTP credentials in private `.env.live` are required to send monitor
registration codes. Nightjar does not store any monitor's personal
Supervisely token after linking their identity.

## Inviting monitors and participants in batches of up to 50

On **People & Teams**, the organiser sees two invitation queues:

- **Monitors → Manager**: all active `monitor` records whose current
  membership status is exactly `not_invited`.
- **Participants → Annotator**: all active `participant` records whose
  status is exactly `not_invited`.

Each queue queries the full event database, not just the current visible
50-row table or the chosen search filter. It returns the first 50 by stable
name/email order, plus a count of uninvited people remaining.

1. Click **Copy N emails** for one group.
2. Paste the copied comma-separated emails in the Supervisely invitation
   interface and assign the shown native role (Manager or Annotator).
3. **After actually sending** the invitations in Supervisely, click
   **Mark batch invited** and confirm. Copying alone never marks sent.
4. The next 50 not-yet-invited addresses automatically appear.

A Nightjar `reported_sent` record counts as invited, even if the recipient
has **not yet joined the Supervisely team**. When someone joins, refresh
from Supervisely to mark them `joined`. Pending verification and joined
status do not put addresses back into the invitation queue.

The app does not claim that copying emails triggers a remote Supervisely
invitation; only the organiser's explicit work in Supervisely does.
Invitation-status reporting is audited and rejects already invited/joined
or mixed-role batches.

## Native API contract

Official Supervisely SDK source:
- `supervisely/api/user_api.py` → `UserApi.change_team_role`, which uses
  `members.editInfo` with `id`, `teamId`, `roleId`.
- `supervisely/api/role_api.py` → `RoleApi.get_list`, using `roles.list`.

The shared token in organiser Settings must have Supervisely **team Admin**
authority to modify other members' roles. Manager credentials cannot
perform this operation. Role mutations are not made when no native
identity has been verified.

### Operations to validate with real Supervisely accounts

Before allowing widespread role changes or event launch, try one
non-owner Manager ↔ Annotator edit and confirm both systems reflect it.
Then test the organiser Admin protection and an intentionally revoked
token. Mocked CI tests validate the contract but do not replace real
Pro-team acceptance tests.
