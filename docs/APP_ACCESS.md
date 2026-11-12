# Nightjar: database-approved self-registration

## Access model

Nightjar app login and event roles are independent. The `approved_staff_emails`
PostgreSQL table is the single source of truth for app access.
Each approved row has `email`, `role` (admin/organiser), and `enabled`.
An Admin has the full app including Organiser functionality. Organisers can
use the app but cannot edit the approved access list. Supervisely and event
Monitor/Participant roles never enable or disable Nightjar credentials.

## First deployment: import the existing list once

Add the approved Admin and Organiser email addresses to private `.env.live`:

    NIGHTJAR_ADMIN_EMAILS=admin1@example.org,admin2@example.org
    NIGHTJAR_ORGANISER_EMAILS=organiser1@example.org,organiser2@example.org

The deployment migration creates `approved_staff_emails`, then
`python -m annotatiathon.manage sync-organiser-access` imports those addresses
ONLY when the database table is empty. After that, all authentication reads
the database, not `.env.live`. Editing the old env vars doesn't replace the
existing database records. For a new approved email on an already-seeded
installation, run:

    python -m annotatiathon.manage approve-email --email new@example.org --role organiser
    python -m annotatiathon.manage sync-organiser-access

Run these inside the `api` container through `/usr/local/bin/nightjar-entrypoint`
as shown in deployment instructions. Existing staff accounts retain their
passwords and are reactivated with correct roles. Staff accounts outside the
approved list are deactivated. No event data, jobs, datasets, or annotations
are removed.

## Self-registration and login

The public login page has `Create account` and `Sign in`.
An approved person enters their own name, approved email and new password
(12 characters minimum) to create their account once.
No registration code, SMTP, email link, Supervisely account or 2FA is used.
The backend rejects unknown emails and refuses to overwrite existing
accounts. Login requires only email and password. Password recovery without
email verification is not available; an administrator can reset credentials
locally with `python -m annotatiathon.manage reset-password` when needed.

**Security limitation:** An address-only allowlist does not prove ownership.
Someone who knows an approved address could register before its rightful
owner and claim the account. This design implements the requested simple
registration model, without falsely claiming to verify mailbox ownership.
Only expose the service to trusted event staff until registration is complete.

## Updating live Nightjar

1. Keep your existing `.env.live` database credentials and encryption key.
2. Set initial approved addresses in `.env.live` before the first deployment
   (the import is skipped if the database list already contains records).
3. Run `git switch main` and `git pull --ff-only origin main`.
4. Run `.\scripts\deploy-live.ps1` to back up PostgreSQL and rebuild the app.
5. Visit your existing Cloudflare Tunnel. No new tunnel or database reset.
6. Verify both Admin logins, one Organiser login, and new self-registration.

Never run `docker compose down -v` on the live application.
