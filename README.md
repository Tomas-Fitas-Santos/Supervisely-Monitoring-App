# Supervisely Monitoring App — Bird Annotatiathon

## November 9 event operations extension

Branch: `feat/video-only-event-ops-20261109`. This branch is based on
`feat/single-team-settings-theme-20261105`, **not** the minimal `main`
branch. DEMO and PRODUCTION share the same application logic and native
Supervisely Team ID (125980); the roster and video dataset inputs differ.

### Rehearsal preparation

1. Use a private two-sheet Excel workbook: exactly `Monitors` and
   `Participants`. DEMO has the nine real staff email addresses on the
   Monitors sheet and zero participant rows initially. Do not commit either
   participant data or Supervisely credentials.
2. Start the live DEMO in Windows (Python 3.12, real API token):
   ```powershell
   git fetch origin
   git switch feat/video-only-event-ops-20261109
   git pull --ff-only origin feat/video-only-event-ops-20261109
   .\\Start-App.ps1 -Mode Demo -Install
   ```
3. In Settings, verify the existing Supervisely team **125980** and its
   actual Workspace ID; use the existing organiser account.
4. In Monitors & Teams import the two-sheet workbook. Before live invitations,
   change two chosen staff event roles to `participant_monitor`, save and
   create the DEMO rehearsal pair. Leave other users as monitors. If an
   account has already accepted membership, an explicit native role-change
   button appears; confirm it and refresh native memberships.
5. Review the identities/roles. Only then send invitations (the app does not
   silently invite on import).
6. In Dataset upload a small real bird-video frame dataset using:
   `Dataset Name/species-or-morphology/video-title/frame_000001.jpg` or
   `species-or-morphology/video-title/frame_000001.jpg`. Use folder or ZIP.
   A complete video is one unit; no `images/` or `videos/` roots.
7. During the rehearsal, verify individual check-in and table number in
   Participant Check-in. In Food & Drinks issue pizza/drink tickets and
   redeem them at reception. A ticket cannot be redeemed more often than
   it was issued. Corrections require reasons and are audited.
8. Verify monitor account permissions and real native annotation submissions
   manually. Do not assume a logical prepared job becomes an actual native
   Supervisely labeling job.

### Important remaining delivery gate

This branch adds roster/roles, reception logistics and the video-only
source importer, but does **not** finish or validate native pair-shared
sequential Supervisely labeling-job creation, automatic next-job release,
or correction return. The existing monitoring code explicitly flags this
missing end-to-end integration. Do **not** declare the full event pipeline
production-ready before testing native jobs with multiple real accounts.

### Quick checks

```powershell
.\\Start-App.ps1 -Mode Demo -CheckOnly
.\\.venv\\Scripts\\python.exe -m pytest -q tests/test_event_people.py tests/test_event_logistics.py tests/test_zip_dataset.py tests/test_role_based_invites.py tests/test_deployment.py tests/test_launch.py
```

After launching the app in another terminal, run `./Test-App.ps1` to
verify the live UI and selected-invite controls. No real invitations are sent
by either preflight command.

Source-data details: [video-only dataset and job planning](docs/structured-dataset-and-jobs.md).
The storage and frame-count caps need to be checked against actual hosting
resources; a Pro subscription does not by itself provide local staging disk.

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
`Start-App.ps1 -Mode Local` without `-Install`. Use
`Start-App.ps1 -Mode Demo` when creating a real-account two-person rehearsal
pair from the imported Monitors sheet. Both modes use the **same** local database,
real Supervisely sign-in and real invitations; DEMO never switches on a fake
simulator. Existing saved event data and encrypted credentials in
`data/account-pilot/` are retained; the directory name is historical.

For a read-only preflight, use `./Start-App.ps1 -Mode Demo -CheckOnly`. After
starting the server, in a **second** PowerShell terminal run `./Test-App.ps1`.
It checks live `/healthz`, the HTML dashboard, Vue bundle and selected-user
invitation controls. The SDK's informational `no-html mode` log does **not**
mean the custom local HTML shell is missing; the smoke test verifies the page.

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

Supervisely account logins may differ from email addresses. If needed, include
`member_1_login`, `member_2_login` and `monitor_login`, or `participant_login`
in a person-row CSV. Reimport preserves invitation evidence and does not
silently change an accepted native role. Optional team-assignment adjustments
are available below the people lists.

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
