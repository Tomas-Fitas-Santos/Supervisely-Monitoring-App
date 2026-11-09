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

Open **http://127.0.0.1:8000**. On later launches, run `Start-App.ps1` without
`-Install`. There is no mode selector. Existing saved event data and encrypted
credentials in `data/account-pilot/` are retained; the directory name is historical.
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

Use **Invite all monitors**, **Invite all participants**, **Invite everyone**,
or **Invite** on an individual's row. People already invited or accepted are
skipped. Refresh invitation status after people accept in Supervisely.
Uncertain invitation results require inspection before retrying.

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
