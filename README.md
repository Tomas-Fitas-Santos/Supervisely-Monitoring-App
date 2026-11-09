# Nightjar Annotatiathon — Event & Annotation Operations

**Clean local-first rebuild** of the Técnico Bird Annotatiathon platform. All development commits go to **`main` only**. No new branches, no demo operation mode, and no real event data in Git.

## What's implemented on `main`

- FastAPI backend, PostgreSQL migrations and real staff account authentication with Argon2 password hashing, HttpOnly sessions, role checks, CSRF and login lockout.
- Responsive React + TypeScript interface: professional login, organiser overview, People & Teams (team/member views, filtering, sorting and editing), CSV mapping with preview/import, select/copy up to 50 emails for manual Supervisely invitations, and organiser-only Supervisely settings.
- Independent local staff authentication: **no Supervisely API token at login**. External Team ID and Workspace ID are configured under Settings.
- Initial automated backend/security tests, real PostgreSQL migration CI and TypeScript build CI.

**Not implemented yet:** actual native Supervisely API connection verification and membership synchronization; QR check-in; tickets/activities; dataset uploads; labeling job planning/dispatch; review/scoring; event finalization. A reported sent invitation is **not** treated as verified native membership. The UI makes these gaps explicit.

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

## Working with the application

1. Open **People & Teams → Import CSV roster** and map CSV columns for name, email, team and optional role. Preview then confirm; an identical reimport does not duplicate attendees.
2. View competition teams or individual people; the Monitoring Team is pinned first. Organisers can add/edit/deactivate people.
3. Select up to 50 people **of one role**, copy comma-separated emails, and send the actual invitations in Supervisely's website. Only then mark those attempts reported sent. Native join and pending-invite verification arrive in the later integration phase.
4. In **Settings**, configure the Supervisely server, team ID (the existing team is 125980) and workspace ID. This does not yet verify API access or alter Supervisely.

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
