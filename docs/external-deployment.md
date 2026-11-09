# External deployment — 9 monitors

## Status
Code is committed but **not yet deployed or verified against real SMTP and Supervisely accounts**. Native pair-shared job creation and automatic sequential job release are still separate integration blockers.

This is a dedicated external application server. All nine monitors use the same HTTPS URL, one PostgreSQL database and one video-only Supervisely event Team (ID 125980).

## Sign-in
Import a two-tab Excel roster (Monitors and Participants). The Monitors sheet is the app login allowlist. Each monitor enters their registered email, receives a 6-digit email code via SMTP, and gets a secure HttpOnly session cookie. Revoking a monitor in the roster invalidates their login. The organiser email configured in EXTERNAL_ORGANISER_EMAIL can sign in before the first roster upload.

**Current integration restriction:** other monitors must also have a verified, accepted Supervisely team membership and corresponding event monitor role. Email-code authentication alone does not currently grant a native event reviewer identity.

## Deploy to a Docker-capable Linux VPS / organisational server

Requires: public DNS pointing to the host, TCP ports 80 and 443, Docker Compose plugin, PostgreSQL credentials, SMTP TLS credentials, and the organiser's Supervisely API token.

1. Clone the repository and checkout feat/external-monitor-login-20261109.
2. Copy .env.external.example to .env.external and replace every placeholder. Generate EXTERNAL_AUTH_SECRET with: python3 -c "import secrets; print(secrets.token_hex(48))"
3. Keep .env.external private. Never commit it.
4. Run:
   docker compose --env-file .env.external -f compose.external.yaml config
   docker compose --env-file .env.external -f compose.external.yaml up -d --build
   docker compose --env-file .env.external -f compose.external.yaml logs -f monitoring
5. Open the configured EXTERNAL_PUBLIC_ORIGIN URL and request a sign-in code.

The Caddy proxy manages HTTPS certificates, and neither PostgreSQL nor the application's internal HTTP port is publicly exposed. Run **one app process / one container** because the Supervisely SDK's dashboard state is process-global and the external shell isolates it per session. Do not autoscale the app without redesigning its state handling.

## First run
Sign in with the organiser email. Set the existing Supervisely Team ID 125980 and its real Workspace ID. Import the DEMO roster, verify monitor roles, and explicitly send/reconcile native invitations where needed. Only then invite other monitors to use the web URL.

DEMO and PRODUCTION use the same features but require **separate PostgreSQL databases**. The demo and official video datasets must not overwrite each other. If both use the real Supervisely Team 125980, they still share native external resources.

## Checks before deployment sign-off
Run Python tests: python -m pytest -q tests/test_external_auth.py tests/test_event_people.py tests/test_event_logistics.py tests/test_zip_dataset.py

Then open two independent browsers, authenticate two distinct monitors, and confirm user-specific team permissions and private dashboards do not leak across sessions. Test expired/replayed email codes, role revocation, two simultaneous ticket redemptions, refresh, logout, application restart and SMTP failure.

A functioning health endpoint is **not** proof of full annotation-job lifecycle readiness.
