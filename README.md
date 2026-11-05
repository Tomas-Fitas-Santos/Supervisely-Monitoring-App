# Supervisely Monitoring App — Técnico Nightjar Annotatiathon

This development branch implements the local **DEMO** and the shared architecture
for **PRODUCTION** using **one pre-existing native Supervisely team** and **one
pre-existing workspace**. No native teams are created by the new workflow.

**Integration boundary:** dataset source uploads, invitation reconciliation and
logical job preparation are implemented but require actual Supervisely
integration validation. Native pair-shared sequential labeling-job activation,
submission/correction return and end-to-end event scoring are not yet complete
for the new single-team architecture. App-level prepared jobs are **not**
native Supervisely labeling jobs.

## Windows quick start

Use Python 3.12 and a Git checkout of
`feat/single-team-settings-theme-20261105`. From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Start-App.ps1 -Mode Demo -Install
```

Open **http://127.0.0.1:8000**. On later launches run
`.\Start-App.ps1 -Mode Demo` without `-Install`.
This is a **live API DEMO**, not offline simulation. The local SQLite database,
encrypted connection and staging are in `data/account-pilot/`.

**Requirements:** Python 3.12, the dependencies from `requirements.txt`
(the launcher installs them), and `ffmpeg` and `ffprobe` for video-frame
conversion. There is no Node build for normal local operation.

For offline-only development, explicitly use
`.\Start-App.ps1 -Mode Simulator`. The simulator has a separate database
and sends no real invitations/uploads.

## Sign in and configure

Connect the organiser's **actual Supervisely numeric User ID**, personal
API token and Supervisely server inside the web app. The configured DEMO
organiser is `tomas.dos.santos@tecnico.ulisboa.pt`, not the older sample
Gmail account. This account must already be an **Admin** of the native event
team. No credentials are included in the repository.

Open **Settings**, verify the existing **Team ID** and its **Workspace ID**.
The login form verifies that the token belongs to the supplied User ID.

## CSV roles: one source of truth before inviting

Import the DEMO roster using **Monitors & Teams → Load bundled demo CSV**,
or select the role-aware `.csv` or `.xlsx` workbook.
The bundled file is `examples/organiser-demo/registrations.csv`.

The pair-row CSV uses `team_name`, `member_1_email`,
`member_2_email`, optional native `member_1_login` and
`member_2_login`, and explicit `member_1_role` and `member_2_role`.
Each pair has two different participants.

| Declared event role | Native Supervisely role requested | In-app responsibility |
| --- | --- | --- |
| `participant` | `annotator` | Annotation work |
| `monitor` (via `monitor_email`) | `manager` | Supervision |
| `participant_monitor` | `reviewer` | Both labeling and reviewing/monitoring |

A **separate monitor** can be provided via `monitor_email` and
`monitor_login`; the same staff monitor can supervise multiple logical
pairs. The CSV may also use **one participant per row**, with
`participant_email`, `participant_role` and `team_name`: two rows
form one logical pair.

The DEMO CSV contains **one competition pair** and both participants are
`participant_monitor` (native `reviewer` role). The organiser remains
the existing native **Admin**, and the CSV never elevates someone to Admin.
The app stores the expected role *before* a native invitation is sent.

**The native Supervisely invitation endpoint accepts existing account
logins, which may differ from email addresses.** If email-only invitation
fails with HTTP 404, confirm the participant's real login with Supervisely
and populate the corresponding `*_login` CSV field. Do not guess it.
A verified account-email mismatch is blocked. Users must have real
accounts on the same Supervisely server. The server may send invitations
by email, or may add accepted members directly depending on the instance.

Click **Send invitations with CSV roles**, then ask users to accept any
pending invitation in Supervisely. **Refresh accepted memberships** verifies
both the real native membership and role; it does not resend invitations.
Pending/unknown outcomes are not retried blindly. Pre-existing accepted
roles are never silently changed on CSV reimport.

## Interface

- **Monitors & Teams**: role-aware CSV/Excel import; native invitations;
  accepted/pending/error status; balanced or manual monitor assignment.
- **Dataset**: browser folder or ZIP containing one named root with
  `images/` and `videos/`; staging, validation, source upload after
  confirmation; logical job distribution preview.
- **Participant Check-in**: physical arrival tracked independently of
  native acceptance, with an audited undo action.
- **Monitoring**: legacy native-job monitoring and review components.
  Their new single-team sequential-job integration is incomplete.
- **Settings**: account/Team ID/Workspace ID configuration and monitor
  registration. The main header provides light/dark mode.

For the required media tree, complete-frame videos, conversion and
job-planner limits, read
[structured dataset and jobs](docs/structured-dataset-and-jobs.md).
For a step-by-step live rehearsal, read
[the current DEMO walkthrough](examples/organiser-demo/README.md).

## Verification

Run the new read-only/local tests from the repository root:

```powershell
.\Start-App.ps1 -Mode Demo -Install
# In a second PowerShell terminal:
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests/test_role_based_invites.py tests/test_zip_dataset.py tests/test_job_design.py
```

These commands do **not** verify that your live instance accepts the
invitation routes, that Reviewer-role users can label a pair-shared job, or
that unfinished native sequential release works. Test real invited
accounts separately before event deployment.

Do not run tests against a production PostgreSQL database: the fixture
drops/recreates its test schema if `TEST_DATABASE_URL` is set.
Do not commit personal API tokens, production attendee lists or private
annotation exports.

`Start-App.ps1 -Mode Event` runs the local event command, whereas
`python -m monitoring.run --production` requires a hosted authenticated
multi-user context and PostgreSQL; the latter is not a production-ready
single-team event release yet.
