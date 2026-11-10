# Nightjar Annotatiathon — Event & Annotation Operations

**Clean local-first rebuild** of the Técnico Bird Annotatiathon platform. All development commits go to **`main` only**. No new branches, no demo operation mode, and no real event data in Git.

## What's implemented on `main`

- FastAPI backend, PostgreSQL migrations and real staff account authentication with Argon2 password hashing, HttpOnly sessions, role checks, CSRF and login lockout.
- Responsive React + TypeScript interface: professional login, organiser overview, People & Teams (team/member views, filtering, sorting and editing), CSV mapping with preview/import, select/copy up to 50 emails for manual Supervisely invitations, and organiser-only Supervisely settings.
- Independent local staff authentication: **no Supervisely API token at login**. External Team ID and Workspace ID are configured under Settings.
- **Attendance & Check-in:** organiser/reception staff can confirm physical arrivals, search and filter attendees, reverse errors without deleting audit history, and view live admission counts.
- **Tickets & Rewards:** categories (Pizza, Drinks, Massage and custom), configurable non-blocking rolling warnings, allocated/issued/redeemed ledger, one person/selected people/teams/all checked-in/all registered batches, immutable recipient preview, idempotent confirmation, per-person balances, full transaction history and controlled audited reversal.
- **Staff Access:** organiser-only multi-role editing; you can add Monitor to your existing organiser staff account without creating a second login. The application refuses to remove the last active organiser.
- Automated backend/security tests, real PostgreSQL migrations and TypeScript build checks.

**Implemented locally:** manual identity-confirmed check-in and shared team table assignments (no QR), activities and prizes, real ZIP/folder dataset import, balanced pair job planning, native job dispatch and status polling, monitor scoring/correction, and closing/exporting events. **Not yet live-verified:** two-account collaborative annotation semantics, native login-based invitation handling, load tests and deployment. Reported invitations are never treated as verified membership.

## Secure self-registration and configuration

- Import the official CSV with monitors marked as `monitor` before inviting monitors to create Nightjar accounts.
- Configure an SMTP server for email verification and a stable `NIGHTJAR_ENCRYPTION_KEY`. Do not put a Supervisely token in `.env`.
- Monitor: choose **Create monitor account** on the Nightjar login page, verify the emailed 8-digit code, set a password, and log in.
- Monitor: under **Settings**, optionally verify their personal Supervisely account by pasting a token. That token is discarded after verification.
- Organiser: save the shared Supervisely Team ID, Workspace ID, and encrypted admin/manager integration token in Settings.
- Distinguish a verified native team member appearing in the roster from an authorised Nightjar monitor login. Only an approved organiser/CSV roster monitor can register.

## Free, real-user live testing (Cloudflare Tunnel)

To share the full Nightjar app with real monitors without paying for hosting,
use the isolated Docker stack and email-protected Cloudflare Quick Tunnel.
It runs FastAPI, PostgreSQL, the built React website and an optional real
Supervisely worker on your own Windows computer. The stack is completely
separate from the existing local development database.

**[Step-by-step Windows live-testing guide](docs/LIVE_TESTING.md)**

```powershell
git pull --ff-only origin main
.\scripts\init-live-test.ps1
docker compose --env-file .env.live -f compose.live.yaml up -d --build web
curl.exe -f http://127.0.0.1:8080/health/ready
```

When ready to share the app, install `cloudflared` and run a Quick Tunnel
restricted to your testers' specific email addresses. Sign in using the HTTPS
tunnel URL; production-mode session cookies are deliberately secure-only.
Do not use this temporary tunnel for the official event.

## Start locally on Windows

Requires Python 3.12+, Docker Desktop with Compose, Node.js 22+ and npm.

Stop any previous Monitoring App occupying port 8000 before starting the new API.

```powershell
git switch main
git pull --ff-only origin main
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env: choose a strong POSTGRES_PASSWORD and set the SAME password in DATABASE_URL.
docker compose -f compose.local.yaml --env-file .env up -d db
py -3.12 -m venv .venv-nightjar
.\.venv-nightjar\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
alembic upgrade head
```

Create the first organiser (no account/password is baked into the code or repository):

```powershell
python -m annotatiathon.manage create-staff --email "organiser@your-domain.pt" --name "Event Organiser" --roles organiser
```

A password is prompted securely; it is not passed on the command line. Organisers are provisioned by this command. **Monitors can instead self-register** from the login page if their email appears as an active `monitor` in the official event roster. They receive an eight-digit code by email and choose their own 12+-character password. An email imported automatically from Supervisely does not qualify for self-registration until explicitly approved in the roster. For other staff roles, use the same command with roles such as `reception` or `activity,distribution`.

Start the backend in **Terminal 1**:

```powershell
.\.venv-nightjar\Scripts\Activate.ps1
uvicorn annotatiathon.app:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in **Terminal 2**:

```powershell
cd web
npm install
npm run dev
```

Open **http://127.0.0.1:5173** and sign in with the organiser account.

API health: `http://127.0.0.1:8000/health/live`, DB health: `/health/ready`. The frontend development server proxies `/api` to port 8000. The API itself does not serve the React interface.

## Upgrading an existing local installation
Keep your PostgreSQL Docker volume and the existing `.env`. From the repository root run:

```powershell
git switch main
git pull --ff-only origin main
.\\.venv-nightjar\\Scripts\\Activate.ps1
alembic upgrade head
alembic current
```

Run `alembic current` to confirm the latest available migration revision. Restart the backend and the Vite development server to see the new Check-in, Tickets & Rewards and Staff Access tabs.

**Windows note:** if your machine's Application Control blocks SQLAlchemy compiled wheels, retain your working pure-Python SQLAlchemy installation; upgrading the app does not require reinstalling SQLAlchemy. Use `npm.cmd` instead of `npm` when PowerShell blocks npm scripts.

## Multiple events, reset safety, and recoverability

- Every staff session selects an event independently using the top-bar selector. Event-specific people, teams, attendance, invitation reports, ticket policies and ticket ledgers are isolated by `event_id`; the first upgrade migrates all existing records to **Initial Event**.
- Organisers can create events and archive/reopen an event under **Settings → Events, archives & resets**. Archived events remain searchable but cannot be mutated.
- **Selective reset** supports attendance, unverified manual invitation reports, ticket balances and people/teams. The organiser must preview affected data, then enter `RESET <exact event name>` within ten minutes. Ticket resets append compensating ledger entries instead of deleting audit history. Roster reset soft-archives identities and enforces ticket dependencies. Re-import can reactivate them.
- **Full fresh start** archives the original event intact and creates a separate empty event with fresh default ticket categories. It never deletes native Supervisely datasets, jobs or annotations.
- Each reset creates an organiser-only private PostgreSQL event snapshot, downloadable as JSON. **These snapshots contain personal data, are not independently stored backups and have no automated restore action yet.** Safeguard the exported file. Use `pg_dump` to make a real off-machine database backup before important tests or production changes.
- Local event resets do not revoke any previously sent native Supervisely invitations. The UI explicitly distinguishes locally reported invites from verified native membership.

## Native Supervisely verification and read-only membership reconciliation

The organiser now configures **Team ID, Workspace ID and the shared Supervisely API token directly in Settings**. The token is verified against the live team, encrypted with `NIGHTJAR_ENCRYPTION_KEY`, and stored in PostgreSQL. The encryption key is a stable application secret supplied by the deployment environment (or an untracked local `.env`) and must be backed up separately from PostgreSQL. The token is never returned to the frontend after submission. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. `SUPERVISELY_API_TOKEN` is no longer used or required in `.env`. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_SENDER` and any needed `SMTP_USERNAME`/`SMTP_PASSWORD` for monitor email verification. The default allowlisted Supervisely host is `app.supervisely.com`.

1. Organiser: in **Settings → Team & Workspace**, save team and workspace IDs; paste a personal admin/manager Supervisely token under **Organiser integration token** and click **Verify and save token**. This uses read requests to `users.me`, `teams.info`, `workspaces.info` and `members.list`. All monitors see the shared IDs but only the organiser can change them.
2. In **People & Teams**, click **Refresh from Supervisely** to update local joined members and add new participants/monitors with a unique native email. Opening the app and relevant pages also triggers a rate-limited sync. To resolve unlinked accounts, use **Verify & reconcile the team** and click **Refresh from Supervisely** there. Review matched identities, role warnings, unlinked native accounts and unmatched roster entries. Email matching is exact; native username and ID are used when already verified. When an email is missing, explicitly associate that account with its roster entry.
3. Confirm **local reconciliation**. The server rechecks the same team, the roster and the remote membership fingerprint and refuses to apply a stale preview. Matched active roster members are marked `joined` and their native login, ID and role are saved in the selected event. Unmatched roster members are left unchanged: absence from `members.list` is not proof of an unsent invitation.
4. The team integration is shared by the event, and all monitor logins can use **Settings → My Supervisely connection** to paste and verify their own token. Personal tokens are **not stored**; the verified account ID, login, and role are linked to the Nightjar staff account. Team ID and workspace ID remain organiser-only. Connection verification and membership reconciliation are read-only; native publishing occurs only after explicit organiser launch and starting the optional SDK worker. Native invitation/role mutations are still not automatic. There is no in-app demo workflow. The SDK semantics are grounded in the official UserApi member list and Supervisely REST endpoints. An actual live token acceptance check on the user's Pro team remains required.

## Event-local monitor responsibilities

In **People & Teams → Monitor assignments**, assign each competition pair to a responsible monitor or preview a balanced distribution. **Fill only unassigned** preserves existing assignments and assigns remaining teams to the least-loaded monitor. **Rebalance all** recalculates responsibility for every active competition team. The organiser reviews the deterministic plan and confirms, with a ten-minute expiry and roster fingerprint validation to prevent stale changes. Manual moves are audited. The Teams list displays the responsible monitor.

These responsibilities are independent of native Supervisely admin/manager roles and **do not modify labeling jobs**. Event archiving makes responsibility data read-only; selective resets can clear just monitor assignments, while resetting People & Teams automatically clears them too.

## Working with the application

1. Open **People & Teams → Import CSV roster** and map CSV columns for name, email, team and optional role. Preview then confirm; an identical reimport does not duplicate attendees.
2. View competition teams or individual people; the Monitoring Team is pinned first. Organisers can add/edit/deactivate people.
3. Select up to 50 people **of one role**, copy comma-separated emails, and send the actual invitations in Supervisely's website. Only then mark those attempts reported sent. Native join and pending-invite verification arrive in the later integration phase.
4. Choose the selected event in the top bar. Under **Settings**, create, archive, reopen and reset event data with a preview and typed confirmation.\n5. Under **Check-in**, receptionist or organiser staff confirm arrivals. Repeat confirmations are safe and reversal is audited.
6. Under **Tickets & Rewards**, choose a category and recipient scope, preview the immutable recipient set, review soft warnings, and confirm. Direct physical issue automatically allocates any missing ticket entitlement; redemption requires outstanding physically issued tickets. Organisers can reverse batches when doing so does not invalidate downstream balances.
7. Under **Staff Access**, organisers can add **monitor** alongside **organiser** on the same staff account. This changes account permissions, not the attendee Monitoring Team listing.
8. In **Settings**, configure the Supervisely server, team ID (the existing team is 125980) and workspace ID. This does not yet verify API access or alter Supervisely.

Only the current screen refreshes its local PostgreSQL view while open, every five seconds. Supervisely roster reads are separately throttled (60 seconds) on app load and navigation, with **Refresh from Supervisely** available in People & Teams and Check-in for an immediate live check. There are no background full-dataset network scans.

## Tests and safety

```powershell
python -m ruff check src tests migrations
python -m pytest -q
cd web
npm run build
```

Do not commit real attendee lists, registration workbooks, media, API tokens, `.env`, exports or database dumps: **the repository is public**. Old branches contain historical registration artifacts and are not merged into `main`.

Architecture, scope and remaining acceptance gates are documented under `docs/`.

## SDK-based Supervisely publishing (optional, live operations)

The local competition queue uses a **separate explicit SDK worker**, not HTTP requests tied to the page. Install with `python -m pip install -e ".[native]"`. Save the encrypted organiser token in Settings, verify the team and workspace from Settings, reconcile both native participant IDs, upload a local dataset, generate and approve the distribution plan, and click **Launch event**. Start the worker in a third terminal: `python -m annotatiathon.native_worker --loop --poll-seconds 12`. For one cycle only, use `--once`. The server remains responsive; no native writes occur until an organiser launches and a worker is running.

Native jobs are published in **two records per two-person team** using `api.labeling_job.create(name, dataset_id, user_ids=[...], images_ids=[...])`. Each pair job receives its own Supervisely dataset of copied image/frame assets, protecting overlapping jobs from other teams from sharing annotation IDs. Both native users receive one native labeling job over that pair-specific dataset. The SDK returns separate job entities and does **not** prove that both users can concurrently edit a single shared answer; perform a live two-account collaboration acceptance test before the official event. Worker errors enter an explicit attention state and never silently retry uncertain remote writes. After inspecting remote state, use the displayed **Retry** command; it reconciles deterministic project/dataset names, media IDs and native jobs rather than blindly duplicating them.

The worker polls two native job statuses. **Both annotators must submit all assigned images AND the monitor must accept the pair job** before the next pair job is released. Local quality scoring and native reviewer actions are separate; returned jobs block subsequent releases. Pause/resume controls affect only future Nightjar releases, not active native Supervisely jobs. The worker never deletes native data, adds members, changes roles or completes jobs on behalf of annotators. See [SDK worker contract](docs/SUPERVISELY_WORKER.md).

## Native annotation archive

After the final accepted jobs are reconciled, close the event in **Results & Exports**. Click **Prepare native annotation export**, and let the optional Supervisely SDK worker download the current JSON annotations. When the task completes, the UI offers a ZIP file containing `full_manifest.jsonl`, `curated_manifest.jsonl`, per-image native annotation JSON, and a README explaining the snapshot. Both manifests retain each source asset SHA-256, morphology, source video group, local team/job identifiers, native image ID and both native job IDs. The curated manifest includes only accepted Nightjar pair jobs and excludes explicitly bad/incomplete sampled frames. Local source media remains in `.nightjar-data` (it is **not** included in the ZIP). The export is **not** a historical annotation-version archive.

## Strict team job lifecycle and monitor-based competition testing

Open **People & Teams → Monitor test pairs** to create a competition team
from exactly two registered monitors. They retain their existing Supervisely
Admin/Manager/Developer roles and remain on the Monitoring Team. You may
disband the local pair **before** planning any labeling job or **after** every planned job has been accepted and its native operations are settled. Once jobs
exist, that pair's historical links must be preserved; use a fresh event
for a new full-workflow test rather than removing annotated jobs.

After uploading a dataset, generate and approve a plan in **Competition &
Jobs**. The organiser explicitly launches the event. Only the **first
pair-level job per team** is published. Both native annotator jobs for
that pair must reach submitted/reviewed status and complete their media.
**Monitoring & Review** then allows frame scoring and an explicit monitor
decision: **Accept** releases the next planned job; **Return for correction**
keeps the same logical job active, queues a native SDK correction/restart and
requires both members to resubmit before continuing. Prior native IDs stay
in the revision-history table and uncertain retries require confirmation.

The SDK creates two native labeling-job rows for two assigned users over
one pair-specific dataset. It does not guarantee that they can concurrently
edit one shared answer; verify that behavior with two live accounts before
the official event. The separate worker command is
`python -m annotatiathon.native_worker --loop --poll-seconds 12`.
