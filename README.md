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

**Not implemented yet:** native invitation dispatch/role changes and pending invitation retrieval; QR-scanner check-in; activities and prize-award automation; dataset uploads; labeling job planning/dispatch; review/scoring; event finalization. A reported sent invitation is **not** treated as verified native membership. The UI makes these gaps explicit.

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

A password is prompted securely; it is not passed on the command line. For additional staff, use the same command with roles such as `monitor,reception` or `activity,distribution`.

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

The new revision is `0006_monitor_assignments (head)`. Restart the backend and the Vite development server to see the new Check-in, Tickets & Rewards and Staff Access tabs.

**Windows note:** if your machine's Application Control blocks SQLAlchemy compiled wheels, retain your working pure-Python SQLAlchemy installation; upgrading the app does not require reinstalling SQLAlchemy. Use `npm.cmd` instead of `npm` when PowerShell blocks npm scripts.

## Multiple events, reset safety, and recoverability

- Every staff session selects an event independently using the top-bar selector. Event-specific people, teams, attendance, invitation reports, ticket policies and ticket ledgers are isolated by `event_id`; the first upgrade migrates all existing records to **Initial Event**.
- Organisers can create events and archive/reopen an event under **Settings → Events, archives & resets**. Archived events remain searchable but cannot be mutated.
- **Selective reset** supports attendance, unverified manual invitation reports, ticket balances and people/teams. The organiser must preview affected data, then enter `RESET <exact event name>` within ten minutes. Ticket resets append compensating ledger entries instead of deleting audit history. Roster reset soft-archives identities and enforces ticket dependencies. Re-import can reactivate them.
- **Full fresh start** archives the original event intact and creates a separate empty event with fresh default ticket categories. It never deletes native Supervisely datasets, jobs or annotations.
- Each reset creates an organiser-only private PostgreSQL event snapshot, downloadable as JSON. **These snapshots contain personal data, are not independently stored backups and have no automated restore action yet.** Safeguard the exported file. Use `pg_dump` to make a real off-machine database backup before important tests or production changes.
- Local event resets do not revoke any previously sent native Supervisely invitations. The UI explicitly distinguishes locally reported invites from verified native membership.

## Native Supervisely verification and read-only membership reconciliation

In the **backend-only** untracked `.env`, set `SUPERVISELY_API_TOKEN` to the token of an authorised organiser account; restart the backend after editing the file. Do not put this token in the browser, chat, frontend configuration, commits or screenshots. The default allowlisted server is `app.supervisely.com`. For trusted enterprise domains, explicitly configure `SUPERVISELY_ALLOWED_HOSTS` on the server.

1. In **Settings → Supervisely**, enter team ID and (optionally) workspace ID, save and click **Verify connection**. This sends only read requests to official Supervisely API endpoints `users.me`, `teams.info`, `workspaces.info` (when configured) and paginated `members.list`.
2. In **People & Teams → Verify & reconcile the team**, click **Refresh from Supervisely**. Review matched identities, role warnings, unlinked native accounts and unmatched roster entries. Email matching is exact; native username and ID are used when already verified. When an email is missing, explicitly associate that account with its roster entry.
3. Confirm **local reconciliation**. The server rechecks the same team, the roster and the remote membership fingerprint and refuses to apply a stale preview. Matched active roster members are marked `joined` and their native login, ID and role are saved in the selected event. Unmatched roster members are left unchanged: absence from `members.list` is not proof of an unsent invitation.
4. **No invitations, role changes, user removal or other remote mutations** occur in this milestone. There is no in-app demo workflow. The SDK semantics are grounded in the official UserApi member list and Supervisely REST endpoints. An actual live token acceptance check on the user's Pro team remains required.

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

Only the current screen refreshes while open, every five seconds. There are no background full-dataset network scans. Switching tabs loads current data immediately.

## Tests and safety

```powershell
python -m ruff check src tests migrations
python -m pytest -q
cd web
npm run build
```

Do not commit real attendee lists, registration workbooks, media, API tokens, `.env`, exports or database dumps: **the repository is public**. Old branches contain historical registration artifacts and are not merged into `main`.

Architecture, scope and remaining acceptance gates are documented under `docs/`.
