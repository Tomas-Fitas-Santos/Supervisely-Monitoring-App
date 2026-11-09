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
5. For uninvited accounts, choose one Supervisely role, select up to
   **50** eligible people, and click **Copy N emails for ROLE**. Paste the
   comma-separated emails into Supervisely **Invite Team members**, choose
   that role, and send the invitation there. Return and click **Refresh
   participants & monitors from Supervisely**. Use **Select next 50** for
   the next batch; copying does not prove an invitation was sent.
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

In **Monitors & Teams → Official registration roster & roles**,
import the source roster as either a single two-sheet Excel workbook
(`Monitors` + `Participants`) **or** upload two separate UTF-8 CSV files
and identify each as `Monitors` or `Participants`. CSV column names match
the workbook schema. Participant CSVs need
`team_name,participant_name,participant_email,participant_role,data_processing_consent`
for useful eligible registrations. Monitor CSVs require
`monitor_email,event_role`. Source fields are preserved. Neither import
sends an invitation or changes native roles.

Choose the one **Supervisely role** to invite: **Annotator** (participant),
**Manager** (monitor), or **Reviewer** (participant_monitor). Select one or
several eligible matching people using the checkboxes. Use **Add matching
eligible (up to 50)** with the sheet/search filter, or **Select next 50**
to prepare successive batches. The app enforces **1–50 unique email
addresses, all with the same saved role**, and excludes already accepted
or pending people, missing emails, unconsented/cancelled registrations,
and role conflicts.

Click **Copy N emails for ROLE**. The clipboard contains **only** emails,
comma-and-space separated, for example:
`first@example.org, second@example.org`. Paste directly in Supervisely's
**New members** box and select the displayed **Role** before clicking
**INVITE**. There are no names, headers, role codes or brackets in the
clipboard data.

**Copying is not confirmation of invitation.** The app records copied batches
only in the current browser session, for **Select next 50** navigation. It
does not grant permissions or mark accounts as invited. After the native
Supervisely invitation, click **Refresh participants & monitors from
Supervisely** to synchronize current membership/invitations. Reset the
copied-batch progress at any time to correct a missed invitation.

Participants with an approved role, valid email, consent and no outstanding
review may be included in an invitation batch **before competition pairs
are prepared**. Pairing/job dispatch remains a separate controlled step.

The older team invitation screen additionally supports **Invite all monitors**,
**Invite all participants**, **Invite everyone**, and **Invite** on an
individual's row. People already invited or accepted are skipped. Refresh
invitation status after people accept in Supervisely. Uncertain invitation
results require inspection before retrying.

Supervisely account logins may differ from registered email addresses.
**Actual cloud invite request confirmed 2026-11-09:** The Supervisely
`app.supervisely.com` website successfully invited a user by their
registered **email**, even though their account has a different username,
using `POST /api/groups/{teamId}/members/add` with JSON
`{"login":"person@example.org","roleId":<native_role_id>,"type":"core"}`.
The old SDK `POST /public/api/v3/members.add` returned 404 on this cloud
instance. The application now sends this observed group request via HTTPS
using its existing organiser's **personal API token** in the `x-api-key`
header, without copying browser Bearer JWTs, session cookies or other
temporary browser credentials.

**Confirmed authentication blocker (2026-11-09):** On the
Supervisely Community/Pro cloud, the captured browser endpoint rejected
the organiser's valid personal API token used alone with **HTTP 401
`Invalid token`**, despite successful Team Admin authentication with the
regular SDK and a browser-successful email invitation. The earlier SDK
`/public/api/v3/members.add` also returned HTTP 404 `Page not found`.
The browser request included a separate Bearer/session credential. Thus
we do **not** have a supported, verified way to send email invitations
programmatically with a personal API key on this instance.

The app now stops a selected batch after its first HTTP 401 rather than
attempting everyone with the same rejected authentication method. It
preserves unattempted selections and reports a per-person API error.
Use **Copy N emails for ROLE** to obtain a comma-separated list of
**up to 50 same-role email addresses only**, invite them through the
[Supervisely Team Members UI](https://app.supervisely.com/members/add),
choose the indicated role, and click **Refresh participants & monitors
from Supervisely**. Never copy browser JWTs/cookies into the Monitoring App.
For automated invites, request a documented service-token-compatible endpoint
or supported OAuth flow from Supervisely support.

**Native roles:** `roleId` is resolved from the actual instance role
directory according to the reviewed CSV role (`annotator`, `manager`,
`reviewer`). The observed `71` is only proof of the browser request that
was made for that one role; the app does **not** force 71 on all invitees.
The `type` is always `core` for this native invite route.

**Invitation behavior:** The exact selected recipient list is used, with
at most one attempt per address. Pending, accepted or unknown outcomes
cannot be silently retried. The native API HTTP rejection and safe provider
message are shown per email. A 404 `Page not found` stops the entire
batch after the first failed request, leaving all later selected accounts
untouched. HTTP 5xx/network errors remain unknown until read-only refresh.

**Refresh participants & monitors:** The existing button reconciles
accepted/pending native team membership and externally invited people
without ever sending another invitation or changing native roles.
New native accounts are imported as unpaired until an organiser creates an
explicit competition pair; reviewed CSV event roles remain authoritative.

**Read-only diagnostic:** `diagnose_invitation.py` reuses the already
encrypted organiser connection, if available. It reads the current team and
does not call the unavailable global `users.list` API or send invitations.

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
