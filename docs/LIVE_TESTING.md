# Free live testing: Windows + Docker + Cloudflare Quick Tunnel

This is **real end-to-end testing**, not a demo mode: Nightjar uses its normal
FastAPI/PostgreSQL application, live Supervisely integration and the real
annotation worker when explicitly enabled. The external HTTPS link costs
nothing; the computer running Docker Desktop must stay powered on.

## Topology and safety

- `compose.live.yaml` defines its own project, `nightjar-live`, with a **separate
  PostgreSQL volume** from `compose.local.yaml`. Never combine the compose files.
- Only the Nginx `web` service publishes a port:
  `127.0.0.1:8080`. FastAPI, PostgreSQL and the native worker stay inside the
  Docker network.
- Nginx serves the compiled React UI and proxies `/api/v1/*` and `/health/*`
  to FastAPI **on the same origin**.
- The media directory defaults to `.nightjar-live-data/` on your computer and is mounted
  at `/data` in the API and native worker. It is NOT backed up automatically.
- `APP_ENV=production` is intentionally forced inside the Docker stack so that
  login cookies have the `Secure` flag. **Sign in through the tunnel's HTTPS
  URL**, not plain HTTP localhost. Use localhost only for health checks.
- The public HTTPS URL opens the **Nightjar login page directly**.
  Nightjar's FastAPI authentication restricts dashboard and operational API
  access using secure HttpOnly session cookies and server-side staff roles.
- Existing staff log in with their Nightjar email/password. A new monitor
  clicks **Create monitor account**, enters an email already approved in the
  active SQL event roster, chooses a Nightjar-only password, then links
  Supervisely later in Settings. Participants cannot self-register.
- Cloudflare Quick Tunnel is launched **without** `--allowed-mail` because
  that option has produced plain 401 responses on login POST requests:
  https://github.com/cloudflare/cloudflared/issues/1752
- This makes the *login page* publicly accessible to anyone with the
  temporary tunnel URL. Account access still requires real credentials or
  an approved roster email and a Nightjar password. **The app does not verify
  control of the entered email:** someone who knows an unclaimed roster email
  could claim that account first. Use only a trusted, small test audience,
  non-sensitive data and a short tunnel session. For the official event, add
  an organiser-issued one-time invite code or another ownership check.
- No ports or port-forwarding need to be opened in your router. The URL is
  temporary and stops working when `cloudflared` exits.

## 1. Prerequisites on Windows

- Docker Desktop running (Linux containers; WSL2 recommended).
- Git, PowerShell and enough free disk space for uploaded bird media.

Run commands below in the repository **root**, not inside `web`:

```powershell
git switch main
git pull --ff-only origin main
docker version
docker compose version
```

## 2. Generate private live-test secrets (one time)

```powershell
.\scripts\init-live-test.ps1
notepad .env.live
```

The script creates a **new**, private `.env.live` with a cryptographically
random database password and stable Fernet encryption key. It never overwrites
an existing file or prints the secrets. This file is ignored by Git.

For large video datasets, change `NIGHTJAR_MEDIA_DIR` in `.env.live` to a
local folder **outside OneDrive**, for example `D:/NightjarLiveMedia`, and
create that folder on Windows before starting Docker. Keep the path consistent
across restarts. The default repository-local directory is fine for small tests.

**Do not change `NIGHTJAR_ENCRYPTION_KEY` later**: it protects the organiser's
Supervisely token in the PostgreSQL database. Never paste secrets or the full
`.env.live` contents into GitHub, a chat, a screenshot or a public log.

You **do not** need `SUPERVISELY_API_TOKEN` in any `.env`. The organiser
will paste the shared native API token directly into Settings.

## 3. Launch the isolated live stack

```powershell
docker compose --env-file .env.live -f compose.live.yaml up -d --build web
docker compose --env-file .env.live -f compose.live.yaml ps
curl.exe -f http://127.0.0.1:8080/health/ready
```

Expected health response: `{"status":"ok"}`.

The website at `http://127.0.0.1:8080/` should display the Nightjar
login page **without any Nginx or Cloudflare password prompt**.
Do not attempt to sign in over local HTTP because secure session cookies
are enabled; sign in using the HTTPS tunnel URL.

The first start builds the API and web images, initializes an isolated
PostgreSQL database and automatically applies Alembic migrations. The separate
native worker is **not** started yet, so it cannot trigger Supervisely job
operations before you configure credentials.

If startup fails, inspect the logs:

```powershell
docker compose --env-file .env.live -f compose.live.yaml logs --tail=100 migrate api web db
```

This live stack has its **own PostgreSQL volume**. Existing local-development staff
logins, rosters and datasets are deliberately not copied automatically.
**All live-test monitor and organiser accounts (including their password
hashes) persist across rebuilding the containers, PC restarts, and using a
different Cloudflare URL as long as this same PostgreSQL volume is retained.**
They simply sign in again with their existing Nightjar email and password;
do not register a second time. Passwords are never stored as plaintext.
Account access is controlled by the persistent staff-account roles/active flag,
not re-checked against another event's registration roster after signup.
Changing the PostgreSQL volume, explicitly deleting/resetting staff records, or
deleting the stack with `down -v` would break this continuity.
The existing `compose.local.yaml` data remains untouched.

## 4. Create the first organiser account

Use your actual organiser email but do not include any passwords on the
command line:

```powershell
docker compose --env-file .env.live -f compose.live.yaml run --rm --no-deps api python -m annotatiathon.manage create-staff --email "organiser@your-institution.pt" --name "Event Organiser" --roles organiser
```

The command prompts securely for a new password. Do this only once per fresh
live-test database.

## 5. Open Nightjar directly through a free HTTPS tunnel

In a **second** PowerShell terminal:

```powershell
winget install -e --id Cloudflare.cloudflared
cloudflared --version
cloudflared tunnel --url http://127.0.0.1:8080
```

Copy the new `https://....trycloudflare.com` URL printed by Cloudflare.
Opening it should lead **directly to the Nightjar sign-in page**. There
is no extra username/password pop-up or Cloudflare email challenge.

- **Existing organiser or staff account:** enter your Nightjar email
  and password. Supervisely credentials are not used for Nightjar login.
- **Approved new monitor:** obtain your one-time private code from an
  organiser, click **Create monitor account**, enter the approved email and
  code, and choose/confirm a Nightjar-only password. No email verification or SMTP is required.
- **Unlisted or ineligible person:** cannot self-register. Participants
  cannot create monitor accounts. Unverified native records require
  organiser approval; verified joined native managers/admins on the active
  SQL monitor roster can register unless explicitly denied.

The temporary tunnel URL is *publicly reachable*, though Nightjar's
application data and operational endpoints remain protected by its own
server-side login and role checks. Use only small authorised test datasets
and don't leave the tunnel running unnecessarily. Cloudflare Quick
Tunnels have no uptime guarantee, support a maximum of 200 in-flight
requests, and do not support SSE. For the official event, use managed
hosting with stronger network protections and load testing.

## 6. Configure real Supervisely and monitor registration

Inside the **HTTPS tunnel**:

1. Sign in as organiser. Open Settings.
2. Enter the actual Supervisely Team ID and Workspace ID (organiser-only).
3. Paste the organiser's admin/manager API token in Settings and verify it.
   The backend encrypts the shared token; monitors only see read-only IDs.
4. Import the official monitor/participant registration roster as CSV.
5. In **Staff Access**, generate a one-time code for the approved monitor's email
   and send it privately. Have that monitor choose **Create monitor account**
   and enter the code and their Nightjar-only password. An active monitor **already verified as a joined
   Supervisely manager/admin in the SQL roster** can register directly without
   another manual approval, unless the organiser explicitly denied registration.
6. The monitor logs into Nightjar independently and can optionally verify
   their own Supervisely identity in personal Settings.
7. In People & Teams, use **Refresh from Supervisely** to reconcile actual
   joined participants and monitors; unresolved identities require organiser
   review.

Do not run native job dispatch until the shared configuration is valid.
A signed-in organiser account alone does not prove Supervisely permissions.

## 6A. Monitor self-registration (no email service required)

1. Import the event CSV using `event_role` for the Role mapping. Monitors
   must have active, approved `monitor` entries with matching email addresses
   in the current SQL event roster. Participants are ineligible.
2. CSV-imported monitors are eligible unless the organiser has disabled them.
   Verified joined Supervisely team managers/admins auto-imported into the
   current event's SQL monitor roster are also eligible unless explicitly
   denied by the organiser. Other auto-discovered records need manual approval.
3. As organiser, generate the person's code in **Staff Access** and send it
   privately to that monitor. The monitor opens the HTTPS link and selects
   **Create monitor account**, entering their listed email, private one-time
   code, and their own 12+-character password twice.
4. A single `POST /api/v1/auth/register` checks SQL roster approval and
   atomically consumes the organiser-issued, single-use registration code before
   creating the Nightjar Monitor login. Codes expire after seven days. It does **not**
   create or join a Supervisely account and does not request any token.
5. On successful registration, Nightjar signs them into the application.
   In **Settings → My Supervisely connection** they can later paste their
   own Supervisely API token to verify/link their team membership. That token
   is never stored; the organiser controls the shared Team ID and Workspace ID.
6. Accounts are unique: if an account has already been claimed for that email,
   registration stops with an existing-account message. Only the organiser
   can approve/deactivate monitor eligibility in the roster.
7. No SMTP or personal Supervisely token is required to register. The organiser
   must issue a private one-time code from Staff Access; the code is stored only
   as a digest, shown once and expires after seven days.

**Security trade-off:** The organiser-issued registration code prevents
first-claim registration using a known roster email alone. Codes are **not**
email-ownership proof: send them privately and never post the codes in a public
channel. Existing Nightjar credentials remain independent of Supervisely.

If registration is rejected, check whether the email is an active approved
monitor in the active event, not a participant, not auto-discovered without
approval, and not already registered. A password must be 12+ characters.

Role edits in People & Teams remain server verified against Supervisely for
joined members. Pending invitations cannot silently change native roles.
Nightjar signup itself is independent of native membership.

## 7. Enable the real Supervisely worker only when ready

Once the organiser has verified the shared team/workspace and token:

```powershell
docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --build worker
docker compose --env-file .env.live -f compose.live.yaml --profile native logs -f --tail=80 worker
```

The worker polls native job status and processes jobs that the organiser
explicitly launched. It is not needed for roster synchronization.

To pause the worker without stopping the app:

```powershell
docker compose --env-file .env.live -f compose.live.yaml --profile native stop worker
```

For a real acceptance test, use two distinct Supervisely annotator accounts,
a small authorized bird dataset and actual jobs. Validate the two-person
completion and monitor-review rules **before** scaling up. Do not claim that
simultaneous collaborative editing is supported until verified live.

## 8. Stop, resume and preserve state

```powershell
# Stop all running containers; retains the live PostgreSQL volume and media
docker compose --env-file .env.live -f compose.live.yaml --profile native stop

# Resume core application on a later day
docker compose --env-file .env.live -f compose.live.yaml up -d web

# Check the application
curl.exe -f http://127.0.0.1:8080/health/ready
```

Stopping the Cloudflare terminal immediately closes the external HTTPS URL.
Never use `docker compose down -v`: `-v` **deletes the live-test database**.

Back up BOTH the `nightjar-live_live_pg_data` Docker volume using PostgreSQL
`pg_dump` and the host `.nightjar-live-data/` directory before important
imports, migrations or tests. Keep `.env.live` / the encryption key securely
backed up separately. Dataset paths in this setup live inside the Docker
network; raw local Windows paths from the earlier development database are
not valid inside containers.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Tunnel URL unavailable | Docker Desktop, `web` container, Cloudflare terminal |
| Extra browser password prompt | Update and rebuild `web`; no Nginx gate is required |
| Nightjar login returns JSON 401 | Check existing account/password and account active/locked state |
| Nightjar login returns plain 401 | Restart the Quick Tunnel without `--allowed-mail` |
| Monitor registration returns 403 | Check the active event SQL roster email, role, active status and organiser approval |
| Monitor registration returns 409 | Nightjar account already exists for that email; sign in |
| New monitor cannot see Supervisely as linked | Sign in first, then verify their personal Supervisely account in Settings |
| Browser login succeeds but session doesn't persist | Use HTTPS tunnel URL, not localhost HTTP |
| Supervisely sync fails | Configure team, workspace and organiser token in Settings |
| Existing local data missing | Live-testing database/media are isolated from local development |

For the official event, deploy the same API/web images and persistent storage
pattern to a properly sized server with stable DNS, TLS, backups and load tests.
Quick Tunnels are deliberately not suitable for 131 pairs and large uploads.
