# Nightjar Production App Access

Nightjar login is independent of Supervisely roles and event-roster Monitor/Participant classification.

## Roles
Production app access is governed by private .env.live configuration:
- NIGHTJAR_ADMIN_EMAILS: comma-separated emails for full admins
- NIGHTJAR_ORGANISER_EMAILS: comma-separated emails for organisers
Admins receive both admin and organiser privileges so they can reach all existing pages.
No other email can log in even if it has an old staff account or a participant/monitor roster entry.

## Existing staff
Live Compose migration runs python -m annotatiathon.manage sync-organiser-access.
This restores active status and correct roles for already registered approved accounts,
keeps their passwords, clears login locks, and revokes obsolete sessions.
Non-allowlisted accounts are deactivated and their sessions revoked. Missing
approved accounts are reported as needing account setup, not given a default password.

## Code-free email setup and recovery
Create organiser account and Forgot password? send single-use email links to approved addresses.
The email link expires in 20 minutes and is not returned to the browser.
A person must control the mailbox to create or reset the account. A known email alone isn't proof.
Set these private values in .env.live:

    NIGHTJAR_PUBLIC_URL=https://your-cloudflare-public-host.example
    NIGHTJAR_SMTP_HOST=smtp.your-provider.example
    NIGHTJAR_SMTP_PORT=587
    NIGHTJAR_SMTP_USER=your-smtp-user
    NIGHTJAR_SMTP_PASSWORD=your-smtp-secret
    NIGHTJAR_SMTP_FROM=verified-sender@example.org

Port 587 uses STARTTLS; port 465 uses implicit TLS.
Without SMTP, link setup returns an explicit 503 and cannot silently grant an account.
An administrator may securely create an allowlisted account using the create-staff CLI
with a privately delivered password, then run sync-organiser-access to reconcile its role.

## Rollout
1. Add the two Admin and seven Organiser addresses to your private .env.live file.
2. Configure SMTP and the public Nightjar URL if you want self-registration and recovery.
3. Run docker compose --env-file .env.live -f compose.live.yaml up -d --build web
4. Run docker compose --env-file .env.live -f compose.live.yaml ps
5. Verify one Admin login, one Organiser login, new email setup, and password recovery.

The old monitor registration-code route is disabled in production.
Event membership edits cannot deactivate staff login; app authorisation is separate.
Never run docker compose down -v on the live stack.