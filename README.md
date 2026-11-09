# Supervisely Monitoring App — Bird Annotatiathon

## Production: import members invited directly in Supervisely

This application uses the existing shared Supervisely team and its live native
permissions. **No demo mode or simulator is used for event operations.**
The active source branch is `feat/video-only-event-ops-20261109`; the repository
`main` branch only contains a placeholder README.

1. In Supervisely, open **Team members** for the existing event team (Team ID
   **125980**) and invite the accounts using their real Supervisely logins.
   Set native **Manager** for monitors, **Annotator** for participants, or
   **Reviewer** for a participant who also monitors. The organiser keeps Admin.
2. In the monitoring app, open **People & invitations** and click
   **Refresh participants & monitors from Supervisely**. It reads accepted and
   pending team members without issuing any new invitation or changing roles.
   It also reconciles existing workbook registrations and previously saved pairs.
3. People invited through the Supervisely web interface now appear in the app's
   Monitors or Participants roster. If an account's email is withheld by the API,
   open **Source columns & Supervisely login**, supply its verified registered
   email, choose **Link native account**, and refresh.
4. To begin using accepted annotators, choose two **unassigned, accepted**
   participants and register one explicitly named competition pair. The organiser
   confirms the registrations and required consent. Existing competition pairs
   and monitor assignments remain unchanged. Alternatively, import the official
   two-sheet workbook and **Prepare complete eligible pairs**.
5. For any people not yet invited, select the exact eligible roster rows and
   choose **Invite selected (N)**; a confirmation shows the recipients. No
   bulk invitation is automatic.
6. Continue to **Dataset**, **Event rules**, **Check-in** and **Monitoring**.

### Windows local administration (real team, no sample data)

```powershell
git fetch origin
git switch feat/video-only-event-ops-20261109
git pull --ff-only origin feat/video-only-event-ops-20261109
.\Start-App.ps1 -Mode Local -Install
```

On later starts, use `.\Start-App.ps1 -Mode Local`. For prerequisites only,
use `.\Start-App.ps1 -CheckOnly`. The local address is
`http://127.0.0.1:8000`; sign in with your personal Supervisely user ID and API
token. This is a local operator interface using real production team records,
**not** a test mode. Never commit registrations or API tokens.

### Remaining release verification

Membership synchronization and two-person registration use real team data,
but the entire native labeling-job dispatch/release/correction pipeline still
requires a live multi-account integration test before the event can be called
fully production-ready. No test or local preflight should be represented as
an actual Supervisely invitation or job dispatch.

### Regression checks

```powershell
.\Start-App.ps1 -CheckOnly
.\.venv\Scripts\python.exe -m pytest -q tests/test_native_member_refresh.py tests/test_role_based_invites.py tests/test_event_people.py
```

Source importer and job planner: [structured dataset and jobs](docs/structured-dataset-and-jobs.md).
External multi-user deployment: [external deployment](docs/external-deployment.md).

---

# Supervisely Monitoring App — Técnico Nightjar Annotatiathon

One application manages event setup, invitations, participant check-in and
annotation monitoring. Each event uses one existing Supervisely team and source
workspace. The CSV, dataset and event rules define the setup.

## Start the app

Use Python 3.12. From the repository root on Windows:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Start-App.ps1 -Install
```

Open **http://127.0.0.1:8000**. On later launches, run
`Start-App.ps1 -Mode Local` without `-Install`. Both invocations
use the same local database, real Supervisely sign-in and real team membership.
Existing saved event data and encrypted credentials in `data/account-pilot/`
are retained; the directory name is historical.

For a read-only prerequisite check, use `./Start-App.ps1 -CheckOnly`.
The SDK's informational `no-html mode` log does **not** mean the local
dashboard is missing: the app serves its own HTML shell.

For multiuser access on **Supervisely Pro**, use the repository's existing
external HTTPS hosting path with `Deploy-External.ps1`, Caddy, SMTP-based
one-time login codes and a persistent PostgreSQL database; see
[deployment quickstart](docs/deployment-quickstart.md) and
[external deployment](docs/external-deployment.md). The monitoring app's
built-in event login does not replace native Supervisely membership.

For an Enterprise private-native-app release (or a separately approved public
app), use `Prepare-Release.ps1` to package a compatible Docker image; see
[hosted deployment](docs/production.md). The localhost runner is deliberately
not a public web server. Neither route automatically completes native
pair-shared sequential labeling-job dispatch.
Video-frame conversion also requires `ffmpeg` and `ffprobe`.

Sign in with your Supervisely User ID and personal API token. Browser logins
are independent, last up to 12 hours, and have a Sign out button. The event
admin controls setup and resets. Registered monitors sign in with their own
accounts to review their assigned teams. A saved polling token does not sign
browsers in automatically. Use separate browser profiles for separate accounts
on one computer.

## Set up the event

**Event setup** displays one step at a time, with **Back** and **Continue**:

1. **Connect event**: verify the existing Supervisely Team ID and Workspace ID.
2. **People & invitations**: upload the roster CSV or Excel file. Monitor and
   participant lists appear immediately; uploading does not send invitations.
3. **Prepare data**: upload a dataset folder or ZIP, or use existing source
   datasets. Select teams, preview the distribution, then prepare jobs.
4. **Set event rules**: enter the event name, annotation instructions and scoring.
   Save and freeze the rules when ready, then finish setup.

Completed steps remain accessible. Invitation acceptance does not prevent
preparing the rest of the event. Participant Check-in and Monitoring remain
separate operational views. Only the event owner can configure or reset an event.

### CSV and invitations

Download a CSV template from the upload panel. The simplest format is:

```csv
team_number,team_name,participant_name,participant_email
1,Team A,Ana,ana@example.org
1,Team A,Bruno,bruno@example.org
```

Upload the supplied workbook directly: both **Monitors** and **Participants**
sheets are read together, regardless of sheet order. Empty sheets are skipped.
Team numbers distinguish pairs with the same name. Names, emails and optional
Supervisely logins populate the lists automatically, with no column-matching step.
Incomplete pairs and people missing emails remain visible; correct the source
file and upload it again to complete them. Invitation and check-in status from
the spreadsheet are never treated as verified account evidence.

A separate monitor CSV can use `monitor_name,monitor_email`. Pair-row CSVs using
`team_name,member_1_email,member_2_email,monitor_email` also work. The same monitor
can oversee several teams and is listed and invited once. Each competition team
needs two participants before jobs can be prepared.

Participant columns imply the `participant` role. Optional `member_1_role`,
`member_2_role` or `participant_role` columns may specify `participant_monitor`
when someone also monitors. A person listed in both workbook sheets is recognized
automatically as a participant and monitor, appears in both lists and receives
one native invitation.

| Event role | Supervisely role |
| --- | --- |
| Participant | Annotator |
| Monitor | Manager |
| Participant and monitor | Reviewer |
| Event admin | Existing Admin; never granted through a CSV |

In **Monitors & Teams → Official registration roster & roles**, use the
**Select** checkbox beside each eligible person (on either the Monitors or
Participants sheet). Filter by name, email or team, then use
**Select matching eligible** to add the filtered people to the selection.
Selections persist when switching between sheets. **Clear selection** resets
the choices. **Invite selected (N)** displays an exact-recipient confirmation
and sends only those selected accounts—never the entire roster by default.

Imported participants become eligible for selection only after their complete
two-person pairs are prepared and approved for processing; monitors can be
selected individually. Entries awaiting review, missing email, unprepared
participants, and people already invited or accepted cannot be selected for
another native invitation. Backend validation rejects missing/empty/duplicate,
unknown, or unreviewed selection batches *before* any native write.

The older team invitation screen additionally supports **Invite all monitors**,
**Invite all participants**, **Invite everyone**, and **Invite** on an
individual's row. People already invited or accepted are skipped. Refresh
invitation status after people accept in Supervisely. Uncertain invitation
results require inspection before retrying.

Supervisely account logins may differ from registered email addresses.
**A username is not required in the CSV.** The app first checks
whether its authorised Supervisely user directory contains exactly one
account with the registered roster email, and uses that account's *actual
username* with `members.add.login` and the CSV-defined `roleId`.
This follows Supervisely's documented API contract. If global directory
access is unavailable or no exact email is visible, the app sends the exact
roster email in the login field **once**, because some instances support it.
It does not guess a username from the email prefix or automatically try a
second invitation on failure. You may explicitly provide an exact verified
username in the roster as a fallback.

**HTTP 404 / Page not found:** This is an ambiguous native API
response: it can indicate a missing route, server/SDK compatibility issue
or an unknown account identifier. It is **not evidence that the registered
email is invalid**. The error now reports which identifier was attempted
and directs the organiser to check the Supervisely server and real username.
There is no automatic retry by another identifier.

**Correct invitation errors:** The app makes one native request with
`raise_error=True`, preserving the actual HTTP rejection rather than hiding
it behind the SDK's retry-limit exception. On a definitive HTTP 400, 401, 402,
403, 404, 409, 422 or 429, the roster shows the status, provider message
(when safe), the affected email and corrective guidance. Network/HTTP 5xx
outcomes remain **unknown** and cannot be blindly resent. Invitation errors
also appear under **Invite selected** rather than showing a false success.

**Refresh participants & monitors from Supervisely:** This button performs
read-only reconciliation of the existing team, including members invited
directly on the Supervisely website. Where possible, team-scoped user
profiles fill missing email/login/role fields from `members.list`. Accepted,
pending, mismatched and deactivated statuses are updated; verified monitors
receive app monitoring access. New team accounts appear in the Monitors or
Participants roster without overwriting reviewed CSV roles, assigning
competition partners or sending invitations. Accounts whose email is hidden
require explicit administrator linking before they can join event pairs.
Refresh can be used before or after roster import. Native roles are never
changed by refresh.

If HTTP 404 means your instance will not resolve an email, confirm the
registered account on the same Supervisely server, invite it manually by
username through Supervisely's **Team Members** interface, and use **Refresh**
to import the accepted membership. The app never invents a login from the
email local-part or sends a second invitation automatically.

### Dataset preparation

See [dataset structure and job planning](docs/structured-dataset-and-jobs.md)
for the folder/ZIP layout and complete-video requirements. Staging and validation
happen before explicit confirmation of the Supervisely upload. Prepared jobs
are durable application records. Shared native sequential-job activation,
correction return and end-to-end scoring still require integration completion
and real Supervisely account validation.

### Reset and appearance

Every admin setup step and operational tab offers a reset preview and explicit
confirmation. Resets save event records in a database backup and retain native
teams, memberships, invitations, uploaded files and annotations. The invitation
ledger is retained to prevent duplicate sends on reimport. Active labeling jobs
are checked and stopped before their local records are cleared. An uncertain
stop preserves local records for inspection.

- Reset people clears the roster, assignments, arrivals, jobs and results.
- Reset dataset preparation clears staged upload records, job plans and results;
  existing native sources can be reused.
- Reset rules restores defaults and clears jobs and results.
- Reset check-in clears arrivals only.
- Restart event clears jobs, submissions, judging, scores and closure while
  retaining people, sources and scoring settings. Prepare jobs again.
- Start setup again also clears the team/workspace binding.

Any reset that clears jobs unlocks rules and clears the old deadline. The last
reset backup can be downloaded by the admin; it excludes credentials and native
media files and is not an automatic undo operation.

Light/dark mode applies to the whole page and is saved in the browser. Buttons
wrap on small screens, with separate group-invitation and individual controls.

## Hosting and verification

Local hosting stays on loopback. For shared hosting, use the same application
with Supervisely's authenticated multi-user gateway and persistent PostgreSQL:
[hosted deployment](docs/production.md). Hosting changes authentication and storage,
not event setup or invitation rules.

Install test dependencies and run focused checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests/test_role_based_invites.py tests/test_registration.py tests/test_auth.py tests/test_event_reset.py tests/test_launch.py
```

Tests use local fixtures. Do not set `TEST_DATABASE_URL` to a live event database;
the test fixture recreates that schema. Real-account validation is required
before relying on native job activation or stopping for an event.
