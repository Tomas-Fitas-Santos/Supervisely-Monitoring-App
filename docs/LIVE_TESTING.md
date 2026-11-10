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
  clicks **Create monitor account**, enters their email from the active
  approved event monitor roster, receives a verification code via SMTP,
  and chooses their own password. Participants cannot self-register.
- Cloudflare Quick Tunnel is launched **without** `--allowed-mail` because
  that option has produced plain 401 responses on login POST requests:
  https://github.com/cloudflare/cloudflared/issues/1752
- This makes the *login page* publicly accessible to anyone with the
  temporary tunnel URL. Account access still requires real credentials or
  approved monitor email verification. For security, use only limited
  non-sensitive test data and a few trusted users; stop the tunnel when
  not testing. A Quick Tunnel is not production hosting.
- No ports or port-forwarding need to be opened in your router. The URL is
  temporary and stops working when `cloudflared` exits.

## 1. Prerequisites on Windows

- Docker Desktop running (Linux containers; WSL2 recommended).
- Git, PowerShell and enough free disk space for uploaded bird media.
- For email-verified monitor signup: SMTP credentials from an approved email
  service; the organiser account can be created without SMTP.

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

In `.env.live`, fill the SMTP fields when you want real monitors to create
their own Nightjar accounts. For example, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`. Use real SMTP settings
provided by your institution or email provider; do not guess values.

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

This live stack has its **own empty database**. Existing local staff logins,
rosters and datasets are deliberately not copied automatically, and your
existing `compose.local.yaml` data remains untouched.

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
- **Approved new monitor:** click **Create monitor account**, enter the
  approved monitor email and use the emailed 8-digit code to register.
  SMTP must be configured in `.env.live` or emails cannot be sent.
- **Unlisted or unapproved person:** cannot self-register. Participants
  cannot create monitor accounts. Organisation administrators must review
  and approve auto-discovered native monitors before they can sign up.

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
5. Have one approved monitor choose **Create monitor account** and complete
   the emailed verification code. If the person was auto-discovered from
   Supervisely, approve their Nightjar registration in People & Teams first.
6. The monitor logs into Nightjar independently and can optionally verify
   their own Supervisely identity in personal Settings.
7. In People & Teams, use **Refresh from Supervisely** to reconcile actual
   joined participants and monitors; unresolved identities require organiser
   review.

Do not run native job dispatch until the shared configuration is valid.
A signed-in organiser account alone does not prove Supervisely permissions.

## 6A. Enable monitor account creation for real external testers

This is the normal application login flow. **No pre-created Nightjar staff
accounts are needed for the monitors.** The organiser must first import an
active monitor roster and have access to a working SMTP mailbox.

### Configure an SMTP sender once

Open the private, Git-ignored environment file in the Windows repository
root:

```powershell
notepad .env.live
```

Fill the following settings using an SMTP account your organisation permits.
The exact host, password and sending policies depend on the email provider:

```dotenv
SMTP_HOST=your-provider-smtp-host
SMTP_PORT=587
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-app-password
SMTP_SENDER=your-authorised-sender-address
SMTP_USE_SSL=false
```

Port **587** uses STARTTLS and `SMTP_USE_SSL=false`. For a provider that
specifies SMTP over implicit TLS, configure its **465** port and
`SMTP_USE_SSL=true`. Do not guess or reuse the Nightjar password as the
SMTP credential. Some accounts use an application password, and institutional
email administrators may disable basic SMTP authentication.

Never commit or share `.env.live`. Passwords are read by the **backend
only**; monitors will never see these settings.

Apply the SMTP settings without deleting any existing data:

```powershell
docker compose --env-file .env.live -f compose.live.yaml up -d --force-recreate api
```

### Verify actual email delivery

1. Sign in to Nightjar as the organiser using the HTTPS tunnel link.
2. Open **Settings → Monitor account verification emails**.
3. Confirm the panel shows **Configured**.
4. Click **Send test to my organiser email** and confirm the message
   reaches the organiser inbox (also inspect spam/junk).
   A successful API response means the email was submitted to SMTP,
   **not necessarily delivered** to the inbox.
5. If the test fails, inspect `docker compose ... logs --tail=80 api`
   (do not share logs containing secrets).

### Test self-registration with another real monitor

1. In **People & Teams**, import the event CSV with the **`event_role`**
   column mapped to Role (`monitor` / `participant`, not Supervisely's
   `native_role`). Ensure the monitor's email is spelled correctly.
2. Native-only auto-discovered monitors must be approved explicitly
   using **Edit → Approve for Nightjar registration**. Existing
   authorised CSV monitors are eligible unless expressly denied.
3. Share the current temporary HTTPS URL with one of the approved monitors.
   Keep Docker Desktop and `cloudflared` running.
4. The monitor clicks **Create monitor account**, enters their roster
   email, and requests an eight-digit verification code.
5. They enter the code and create/confirm their own 12+-character Nightjar
   password. The code expires after 10 minutes and can be resent after
   one minute. The account creation is single-use; existing accounts sign in.
6. Nightjar then signs the monitor in using the newly created account.
   Monitor role privileges are server-enforced; a participant's address
   cannot sign up for a monitoring account.
7. In Settings, the monitor sees the organiser's shared Team ID and
   Workspace ID read-only, and can separately verify their own
   Supervisely identity with a personal API token (discarded after
   verification).

A Nightjar staff account is independent of a Supervisely membership:
**registration does not invite that person to Supervisely**. Use the
separate People & Teams native invite workflow to grant Supervisely
Manager access. If a verified native team member is not a Manager, the
Nightjar authorization checks can refuse access until the organiser
reconciles their role.

### Troubleshooting account registration

- **Email verification not ready:** Configure SMTP in `.env.live` and
  recreate `api` as above; test delivery from organiser Settings.
- **No email after requesting code:** The public request intentionally
  gives a generic reply for unknown, ineligible and existing accounts.
  Check the exact roster email, active event and approval status, or
  whether the account already exists; use the Settings test to check SMTP.
- **Verification code expired/invalid:** Request a fresh code. At most
  five incorrect attempts are allowed per challenge.
- **Staff login denied after role change:** Confirm the person is still
  an active approved monitor and, if joined, has the right Supervisely
  Manager role. The organiser remains Admin.
- **Role update appears stuck:** The People & Teams Edit dialog now
  displays the progress and actual failure inside the modal. Native
  member roles are now read individually, not by scanning the entire
  Supervisely team. Refresh membership if a remote write is unverified.

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
| Tunnel URL unavailable | Docker Desktop, `web` container, and `cloudflared` terminal |
| Extra browser username/password prompt | Run `git pull` and rebuild `web`: the Nginx gate was removed |
| Nightjar login returns JSON 401 | Check staff email/password and whether the account is active/locked |
| Nightjar login returns plain text 401 | Start Quick Tunnel **without** `--allowed-mail` (Cloudflare can intercept POST requests) |
| Monitor gets no verification email | SMTP must be configured and monitor must be on active approved roster |
| Browser login succeeds but session doesn't persist | Use the HTTPS tunnel URL, not localhost HTTP |
| 502/503 from frontend | Review Docker logs and `/health/ready` |
| Supervisely sync fails | Configure shared Team ID, Workspace ID and organiser token under Settings |
| Worker repeatedly restarts | Don't enable until the team/workspace is verified |
| Existing local data missing | Live-testing database/media are intentionally isolated from local development |

For the official event, deploy the same API/web images and persistent storage
pattern to a properly sized server with stable DNS, TLS, backups and load tests.
Quick Tunnels are deliberately not suitable for 131 pairs and large uploads.
