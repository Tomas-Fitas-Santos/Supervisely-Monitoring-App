# Supervisely Monitoring App — deployment quickstart

Use the feature branch `feat/video-only-event-ops-20261109`.
It contains the selected-person invitation workflow and both DEMO/PRODUCTION
operational logic. Do not deploy the minimal `main` branch.

## A. Run a local, live-account rehearsal

Requirements: Windows, **64-bit Python 3.12**, Internet access to Supervisely,
an actual organiser account and its personal API token. The app itself listens
on **127.0.0.1:8000 only**; it is not shared web hosting.

```powershell
git fetch origin
git switch feat/video-only-event-ops-20261109
git pull --ff-only origin feat/video-only-event-ops-20261109
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Start-App.ps1 -Mode Demo -Install
```

First launch installs Python dependencies. On subsequent launches, omit
`-Install`. Use `-Mode Local` for ordinary local operation. `-Mode Demo`
enables the **two real staff** rehearsal-pair action; it never turns on a fake
Supervisely simulation. Both modes reuse the same local event database and
recorded invitations; running either does **not** reset anything.

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then sign in with
your own Supervisely **User ID and API token**.

In another PowerShell terminal, from this same checkout:

```powershell
.\Test-App.ps1
```

A PASS verifies `/healthz` reports `local`, the HTML dashboard loads, bundled
Vue loads, and the **Invite selected** frontend is being served. It sends no
invitation and changes no event data. If the SDK prints `Application is
running in no-html mode`, this is not by itself a launch failure; verify the
local HTML shell with `Test-App.ps1`. Also try a hard browser reload after
pulling a new frontend version.

### Set up the rehearsal

1. **Settings:** authenticate the organiser and confirm the existing event
   Supervisely Team ID and actual Workspace ID. Do not create 131 native teams.
2. **Monitors & Teams:** upload the two-sheet workbook (Monitors and
   Participants); review roles. For DEMO, set two existing staff to
   `participant_monitor`, save their roles and choose the rehearsal pair.
3. **Selected invitations:** select accounts using checkboxes or filter and
   **Select matching eligible**, then select **Invite selected (N)** and confirm.
   This creates real native invitations for **only** the selected eligible
   accounts. Refresh membership after they accept. Do not use an "Invite all"
   control for a rehearsal.
4. **Dataset:** upload a small complete video-frame dataset, preview, then
   explicitly upload to Supervisely. Prepare logical jobs.
5. **Monitoring:** verify account permissions, check-in, task ownership and
   annotation submission using real accounts. Logical prepared jobs do **not**
   yet prove actual sequential native Supervisely labeling jobs were created.

## B. Prepare a shared hosted Supervisely release

This app uses Supervisely's native multi-user application session for hosted
identity and **PostgreSQL** for persistent shared state. A publicly exposed
bare Uvicorn process is **not** a supported way to bypass the platform's
authentication. Before spending time building an image, confirm that your
Supervisely plan/instance permits the intended private/native app deployment,
you have a compatible Supervisely Agent and the right to publish an app.

Required independent infrastructure:
- Docker and an image registry **reachable by your Supervisely Agent**.
- A real numeric **Supervisely instance version** (`X.Y.Z`), verified in
  the UI; do not copy a made-up version number.
- A private reachable PostgreSQL database and durable upload staging path.
  `compose.yaml` is a **PostgreSQL-only** helper for a controlled host; its
  `database` hostname does not resolve from an unrelated Supervisely Agent.
- An organiser with the native permissions to run a multi-user application.

From Windows PowerShell, after configuring Docker registry authentication
and installing Python dependencies:

```powershell
.\Start-App.ps1 -Mode Local -Install -CheckOnly
.\Prepare-Release.ps1 -Image "YOUR_REGISTRY/nightjar-monitor:YOUR_TAG" -InstanceVersion "X.Y.Z" -Build -Push
```

The release script builds/pushes **only** when explicitly requested, then
creates `dist/supervisely-release` with a native `config.json`,
`instance_version`, `docker_image` and checksums. It verifies that
`requirements.txt` is not present alongside the release config.
Use a new `-Output` destination when preparing another release; it will
not overwrite an existing release.

Publish the **generated release directory**, not the development repository
root, through your instance's supported private-app mechanism. Supply
`DATABASE_URL` (PostgreSQL) and `UPLOAD_STAGING_DIR` (persistent writable
storage) through hosting secrets/configuration, not Git or uploaded CSVs.
Use **one running hosted application** and a single durable event database.
The `SYNC_API_TOKEN` secret is optional for unattended central polling,
but its permissions must be verified before enabling automatic event closure.

Confirm `/healthz`, native Team/Workspace settings, a monitor's independent
login, and a **single selected real invitation** before onboarding everyone.

Official Supervisely references:
- [Add private app](https://developer.supervisely.com/app-development/basics/add-private-app)
- [Release compatibility](https://developer.supervisely.com/app-development/basics/compatibility)
- [App configuration](https://developer.supervisely.com/app-development/basics/app-json-config/config-json)

## C. Stop conditions before declaring the event ready

The current application **does not yet prove native pair-shared sequential
labeling-job creation, automatic next-job release, or correction return**.
A real multi-account, actual Supervisely dataset/job rehearsal is mandatory
before the event. A green local smoke test or release package does **not**
certify native job dispatch.

Do not commit `.env`, token files, roster data, local database files,
or original event media; the development repo excludes runtime data by default.
