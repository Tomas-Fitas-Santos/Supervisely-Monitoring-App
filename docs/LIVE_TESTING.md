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
- Quick Tunnel access MUST be restricted to specific authorized emails using
  `--allowed-mail`. Anyone permitted through the Cloudflare email challenge
  still needs their separate Nightjar login. Do not expose a bare public tunnel
  containing real event data.
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

## 5. Install Cloudflare Tunnel and restrict by email

Cloudflare supports free, temporary Quick Tunnels with optional email
authentication. Current documentation: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

In a **second** PowerShell terminal:

```powershell
winget install -e --id Cloudflare.cloudflared
cloudflared --version
cloudflared tunnel --url http://127.0.0.1:8080 --allowed-mail "organiser@your-institution.pt,monitor1@your-institution.pt"
```

Replace those placeholder addresses with actual approved testers' emails.
You can repeat `--allowed-mail` to allow more specific people. Restart the
terminal after installation if `cloudflared` is not yet on the path.

Copy the unique `https://....trycloudflare.com` URL shown by the terminal.
Open that HTTPS address. Each tester must complete Cloudflare's email
challenge, then log in to Nightjar.

Keep the PowerShell tunnel process open. The hostname changes each time the
tunnel is restarted. Cloudflare limits Quick Tunnels to 200 in-flight
requests, provides no uptime guarantee, and does not support SSE. This
approach is for live functional testing, **not for hosting the official event**.

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
| Tunnel URL unavailable | Docker Desktop, `web` container, tunnel terminal and its printed hostname |
| Browser opens but login does not persist | Use the `https://...trycloudflare.com` URL; secure cookies won't work on plain HTTP |
| 502 or 503 from frontend | `docker compose ... logs migrate api db web` and `/health/ready` |
| Monitor never gets registration email | Check `SMTP_*` settings and sender permissions; email must already be approved in the active monitor roster |
| Supervisely sync fails | Save shared Team ID, Workspace ID and the organiser's token under Settings; verify the account has access |
| Worker repeatedly restarts | Do not enable it until the shared Supervisely team and workspace have been verified |
| Existing local data not visible | Intentional: live testing uses a separate PostgreSQL volume and media directory |

For the official event, deploy the same API/web images and persistent storage
pattern to a properly sized server with stable DNS, TLS, backups and load tests.
Quick Tunnels are deliberately not suitable for 131 pairs and large uploads.
