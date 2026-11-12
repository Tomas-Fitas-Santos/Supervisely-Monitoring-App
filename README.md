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

**Implemented locally:** manual identity-confirmed check-in and shared team table assignments (no QR), activities and prizes, real ZIP/folder dataset import, automatic continuous 30-frame distribution across video boundaries, native job dispatch and status polling, monitor scoring/correction, and closing/exporting events. **Not yet live-verified:** two-account collaborative annotation semantics, native login-based invitation handling, load tests and deployment. Reported invitations are never treated as verified membership.

## Secure self-registration and configuration

- Import the official CSV with monitors marked as `monitor` before inviting monitors to create Nightjar accounts.
- Keep a stable `NIGHTJAR_ENCRYPTION_KEY` for encrypted shared Supervisely settings. No SMTP or personal Supervisely token is required for Nightjar signup.
- Monitor: choose **Create monitor account**, enter an active approved roster email, the private organiser-issued registration code, and an app-only password; link Supervisely later in Settings.
- Monitor: under **Settings**, optionally verify their personal Supervisely account by pasting a token. That token is discarded after verification.
- Organiser: save the shared Supervisely Team ID, Workspace ID, and encrypted admin/manager integration token in Settings.
- Distinguish a verified native team member appearing in the roster from an authorised Nightjar monitor login. Only an approved organiser/CSV roster monitor can register.

## Video-only dataset import and coverage

The **Dataset** main tab has two subtabs: **Upload & Manage** and **Coverage &
Classification**. Imports use a root dataset folder (or a ZIP containing that
folder) with bird-species folders immediately inside it. There are **no
`images/` or `videos/` source folders** in the new format.

For example, upload `BirdGaze/CorvusCorone/flight_01.mp4` and
`BirdGaze/ColumbaPalumbus/feeding_02.mkv`. Alternatively, if a video is
already expanded into images, upload
`BirdGaze/CorvusCorone/flight_01/frame_0001.jpg` and subsequent frames.
A root-folder selection and ZIP import are validated identically; the optional
outer `BirdGaze/` wrapper is automatically detected. A single species folder
is not mistaken for the wrapper.

For raw video files, choose **All source frames** to extract every decoded frame
into JPEGs or **1 frame per second** to sample. Video frame folders retain the
supplied images; add a [frame manifest](docs/FRAME_IMPORT.md) to declare source
ordinals and sampling density. The original compressed videos are retained privately in the dataset
directory. All new imports are video-specific frame groups internally
(`videos/<species>/<video-title>/<frame>`) because the Supervisely labeling
workflow operates on image frames. An official automatic labeling job contains 30 consecutive stream frames assigned
to one annotator. Frames at a video boundary can be combined in that job (for
example, 14 frames from Video A followed by 16 from Video B). Only the final
dataset-wide job may contain fewer than 30 frames. Manual jobs continue to
select 30 consecutive frames from one video. Source video IDs and frame
ordinals remain attached to every frame. Pre-existing legacy imports remain readable.

Coverage includes unique frames, video groups, species/source folders and the
**16 ecomorphologies** from the Annotatiathon Bird Morphology & Head Motion
workbook. The workbook's 72 known scientific-name source folders are recognized;
unknown folders remain Unclassified until curated. Organisers can correct
classifications with an audit trail. Movement tags record observed primary
movement over disjoint frame ranges; they do not infer movements from species.
Frame ordering uses source ordinals when available and falls back to filename
order for legacy frames. The existing planner targets 120,000 frames, or
7,500 per group.

## Real Supervisely roles and invite batches

Event role → Supervisely team role: **Monitor → Manager**, **Participant → Annotator**,
while the existing Nightjar organiser/monitor remains Supervisely **Admin**.
Editing a joined member's role in People & Teams updates the verified native
team role first; failed remote changes do not change the local role. Reclassifying
a monitor as a participant disables their Nightjar monitor login and
invalidates their sessions.

The People & Teams screen also offers two separate **Copy next 50 uninvited**
queues, one for Monitors/Manager and another for Participants/Annotator,
across all pages of the current event roster. Copying emails does not
pretend to send a native invitation. After sending the invitations on
Supervisely, click **Mark batch invited**; those people remain excluded
even if they have not joined yet. A fresh batch is then available.

See **[Role and invitation contract](docs/ROLES_AND_INVITATIONS.md)**.

**First-claim security caution:** SQL roster matching alone does **not** prove
email ownership. A person knowing another approved monitor's unclaimed email
could register first. Use this simplified flow only for trusted live tests,
and require an organiser-issued one-time invite code or identity proof before
the public event.

## Free, real-user live testing (Cloudflare Tunnel)

Run the normal FastAPI, PostgreSQL and React app on a Windows machine
with an isolated Docker stack and a free temporary Cloudflare HTTPS URL.
The tunnel now opens the **Nightjar login page directly**, with no extra
Nginx password prompt or Cloudflare email challenge.

**[Complete Windows live-testing guide](docs/LIVE_TESTING.md)**

```powershell
git pull --ff-only origin main
.\scripts\init-live-test.ps1
docker compose --env-file .env.live -f compose.live.yaml up -d --build web
curl.exe -f http://127.0.0.1:8080/health/ready
```

Open a second PowerShell terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8080
```

**Existing staff** sign in with their Nightjar accounts. **Approved monitors**
can create their own accounts using the **Create monitor account** button,
the current active SQL event's monitor roster (without email verification). No
participant gets a monitor account. The app enforces roles and session
authentication on the API.

This exposes the login page publicly on a temporary HTTPS URL, so limit
live tests to trusted users, small authorised datasets and short sessions.
It is **not** a secure or reliable hosting plan for the official event.

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

A password is prompted securely; it is not passed on the command line. Organisers are provisioned by this command. **Monitors can instead self-register** from the login page with an active approved roster email and the organiser-issued one-time code. They choose their own 12+-character app password; no email-service integration is required. An email imported automatically from Supervisely does not qualify for self-registration until explicitly approved in the roster. For other staff roles, use the same command with roles such as `reception` or `activity,distribution`.

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

The organiser now configures **Team ID, Workspace ID and the shared Supervisely API token directly in Settings**. The token is verified against the live team, encrypted with `NIGHTJAR_ENCRYPTION_KEY`, and stored in PostgreSQL. The encryption key is a stable application secret supplied by the deployment environment (or an untracked local `.env`) and must be backed up separately from PostgreSQL. The token is never returned to the frontend after submission. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. `SUPERVISELY_API_TOKEN` is no longer used or required in `.env`. The default allowlisted Supervisely host is `app.supervisely.com`.

1. Organiser: in **Settings → Team & Workspace**, save team and workspace IDs; paste a personal admin/manager Supervisely token under **Organiser integration token** and click **Verify and save token**. This uses read requests to `users.me`, `teams.info`, `workspaces.info` and `members.list`. All monitors see the shared IDs but only the organiser can change them.
2. In **People & Teams**, click **Refresh from Supervisely** to update local joined members and add new participants/monitors with a unique native email. Opening the app and relevant pages also triggers a rate-limited sync. To resolve unlinked accounts, use **Verify & reconcile the team** and click **Refresh from Supervisely** there. Review matched identities, role warnings, unlinked native accounts and unmatched roster entries. Email matching is exact; native username and ID are used when already verified. When an email is missing, explicitly associate that account with its roster entry.
3. Confirm **local reconciliation**. The server rechecks the same team, the roster and the remote membership fingerprint and refuses to apply a stale preview. Matched active roster members are marked `joined` and their native login, ID and role are saved in the selected event. Unmatched roster members are left unchanged: absence from `members.list` is not proof of an unsent invitation.
4. The team integration is shared by the event, and all monitor logins can use **Settings → My Supervisely connection** to paste and verify their own token. Personal tokens are **not stored**; the verified account ID, login, and role are linked to the Nightjar staff account. Team ID and workspace ID remain organiser-only. Connection verification and membership reconciliation are read-only; native publishing occurs only after explicit organiser launch and starting the optional SDK worker. Native invitations remain manual: role changes to already-joined members ARE synchronized through verified Supervisely `members.editInfo` when the organiser edits them in People & Teams. There is no in-app demo workflow. The SDK semantics are grounded in the official UserApi member list and Supervisely REST endpoints. An actual live token acceptance check on the user's Pro team remains required.

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

## Supervisely video preloading, automatic jobs and manual dispatch

The **separate SDK worker** performs real Supervisely writes; browser requests
enqueue durable operations in PostgreSQL. Keep the encrypted organiser token,
verified team and workspace configured under Settings and run the native
worker with `python -m annotatiathon.native_worker --loop --poll-seconds 12`
(or via the native Docker Compose profile). Publishing is not part of an HTTP
request and does not require the browser to stay open.

**Pre-event:** Upload a video-only dataset by root folder or ZIP. Local
finalisation automatically queues a *full mirror* of all source video frames
into a single Supervisely image dataset, named **Complete video library · all
species**, within Nightjar's event-owned project. This mirror creates a stable
source-frame-ID → remote-image-ID mapping in `native_source_media`, checks
the nine bird box/visible/occluded keypoint classes, uploads in batches with
idempotent recovery, and publishes a completion status. Dataset → Upload &
Manage shows progress and an explicit Retry action if remote synchronization
is interrupted. **Automatic launch is refused until the entire dataset is
mirrored.** No annotation jobs are created during the dataset import.

**Automatic distribution:** Every selected complete video goes to **exactly
one person across the whole event** (no redundant teams/overlap). Every
two-person competition team receives different videos, at least one per
teammate. One Supervisely labeling job is created per video/person using only
the existing `images_ids` subset of the shared, pre-uploaded library.
No video/image frames are uploaded while automatically publishing jobs.
Each annotator's next job is released after the previous submission is
accepted. An explicit organiser-approved plan is still required.

**Manual dispatch during the event:** In Competition & Jobs → Create video
jobs, choose the team, select the video(s), then choose which of its two
annotators gets each video. Every row creates one separate native job. Default
**Lower priority** keeps the normal worker order; optional **Higher priority**
moves manual jobs ahead of normal pending publishing operations. An explicit
**Allow duplicate videos** switch permits a deliberate repeated manual
assignment. Manual jobs use a *separate Supervisely dataset*, **Manual
assignments · independent video copies**, with image names scoped to the
individual Nightjar job, so duplicate videos never share annotation state.
The worker uploads only these deliberate manual copies, not new automatic
video copies. Held manual jobs are possible and can be released later.

**Reassignment:** An organiser can move an unsubmitted job to any verified
Supervisely annotator, even from another team. A queued, unstarted job can
be updated locally; an already published job uses the SDK's
`labeling_job.set_assignees` and reads the updated native job back before
changing Nightjar's team, monitor and person association. Uncertain
publishing/submitted/reviewed jobs cannot be reassigned by this control;
failed native operations retain an attention/retry state.

**Submission and review:** The worker obtains **only submitted annotation
JSON** via the corresponding native labeling-job endpoint, not images and
not a shared project-level annotation read. It writes immutable per-frame
JSON snapshots referenced to source media ID, local source SHA-256, job ID,
and native image ID. Nightjar renders these objects over the *original
locally stored images* for monitor review. The same source image can have
multiple independent manual annotation submissions without mixing them.
Pausing automatic releases does not pause an existing native job.
No Supervisely team membership changes are made by job dispatch.

## Native annotation archive

After the final accepted jobs are reconciled, close the event in **Results & Exports**. Click **Prepare native annotation export**, and let the optional Supervisely SDK worker download the current JSON annotations. When the task completes, the UI offers a ZIP file containing `full_manifest.jsonl`, `curated_manifest.jsonl`, per-image native annotation JSON, and a README explaining the snapshot. Both manifests retain each source asset SHA-256, morphology, source video group, local team/job identifiers, native image ID and both native job IDs. The curated manifest includes only accepted Nightjar pair jobs and excludes explicitly bad/incomplete sampled frames. Local source media remains in `.nightjar-data` (it is **not** included in the ZIP). The export is **not** a historical annotation-version archive.

## Monitor-pair testing and competition lifecycle

People & Teams supports test pairs made of two existing monitors without
changing their Supervisely manager/admin privileges. Each person has a
separate native labeling job and receives a different complete video when
automatic scheduling is used. The same team and event are reused for manual
jobs during the live event; no separate event is needed.

In Monitoring & Review, every monitor can see all teams' dashboards and
provisional rankings. Assigned reviewers can inspect the local source frame
with Supervisely JSON boxes/keypoints overlaid, score the submitted points,
and Accept or Return the job for correction. The native correction history
remains tied to the job, and only accepted work releases an annotator's
next automatic job. A team's assigned monitor can be changed by an organiser
without moving the underlying native annotation data.
