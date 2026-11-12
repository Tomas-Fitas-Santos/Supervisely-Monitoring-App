# Verify every approved Nightjar Admin and Organiser login

Nightjar **staff authentication** uses its own PostgreSQL staff accounts.
A Supervisely account, join status, API token, team role or event-roster role
is not needed to sign in as a Nightjar Admin or Organiser.

## What the Admin can check

In the Nightjar **Staff Access** tab, click **Refresh login status**:

- **Ready to sign in**: the approved address has a registered, active staff
  account, the stored roles agree with the enabled approval and the account
  is not temporarily locked. **This does not verify the user's password.**
- **Login verified**: the above is true and Nightjar has recorded at least
  one successful staff login for this account. The time shown is the last
  successful login, not a guarantee that subsequent attempts will work.
- **Not registered yet**: an approved email does not yet have a staff
  account. The person must click **Create account** on the sign-in page and
  set a Nightjar-specific password. Their assigned Admin or Organiser role
  comes from the approved-email database table, not the event roster.
- **Temporarily locked**: five unsuccessful password attempts can lock
  an account for 15 minutes. Do not keep retrying. An authorised operator
  can use the existing password-reset CLI for legitimate recovery.
- **Account disabled**, **Access disabled**, **Role needs reconciliation**:
  the staff record and approval table need administrator attention.

The staff list and login timestamps are visible to **Admin** accounts, not
ordinary Organiser accounts.

## How to perform a real nine-person acceptance check

For example, for a group of two Admins plus seven Organisers:

1. The Admin checks the list contains nine **enabled** approved emails,
   with the expected two Admin and seven Organiser roles.
2. Every registered person opens the **same HTTPS Nightjar login URL**,
   signs in with their own existing Nightjar password, and confirms the
   event overview loads.
3. Each unregistered person clicks **Create account**, enters the *exact*
   approved email and their own new password (12+ characters), and signs in.
   The app does not email a verification code, and does not contact Supervisely.
4. The Admin clicks **Refresh login status** and checks that all nine
   show **Login verified** and their most recent successful login timestamp.
5. Check one Organiser cannot open **Staff Access**, but can use their
   permitted operations pages. Check one Admin *can* open **Staff Access**.
6. If someone fails, inspect the HTTPS browser response and Docker API logs,
   then check their particular record for an existing login, lockout, role
   mismatch or disabled access. **Never ask people to share their passwords.**

**Security limitation:** direct approved-email signup does **not** verify
who controls that email. Someone who knows an approved, unclaimed email
could claim the account before its intended owner. This is a known risk
for externally exposed test links. For official-event deployment, adopt an
organiser-issued single-use invitation or a verified identity provider.

## Corrected email already has an existing account

If an organiser's email was entered incorrectly, `change-staff-email`
changes the email of their existing Nightjar staff account **only if no
account already exists at the corrected address**.

If it reports `A staff account already exists under the corrected email;
no accounts were merged`, the source account remains unchanged. To retain
the **existing corrected account** and disable the obsolete email's access
without losing either record, run:

```powershell
docker compose --env-file .env.live -f compose.live.yaml run --rm --no-deps api python -m annotatiathon.manage keep-existing-staff-account --obsolete-email "old@example.org" --keep-email "corrected@example.org"
```

This command requires the obsolete email to be enabled in the approval
database and requires the corrected account to be active with the same
Admin or Organiser role. It preserves the corrected account's password
and user ID, creates its approved-email entry if missing, deactivates
the obsolete account, disables its approval and revokes obsolete sessions.
It never merges or deletes the accounts. A conflicting or disabled
destination causes an error without changing anything. The organiser
then uses the corrected email and **the password already associated
with that corrected account**, not the old account's password.

Because `docker compose exec api` skips the API container's startup
entrypoint, use `docker compose run --rm --no-deps api` for the CLI.

## Updating the live-testing Docker stack

From the repository root, with Docker Desktop running:

```powershell
git pull --ff-only origin main
docker compose --env-file .env.live -f compose.live.yaml up -d --build api web
curl.exe -f http://127.0.0.1:8080/health/ready
```

No database reset, password reset, SMTP, Cloudflare account or Supervisely
relinking is required for these changes. Keep the existing Cloudflare
Quick Tunnel running if already connected, otherwise start a fresh tunnel
without `--allowed-mail`.

## What automated tests prove—and don't prove

`tests/test_organiser_access.py` constructs a disposable SQL test database,
approves two Admins plus seven Organisers, logs in seven existing accounts,
registers and logs in two pending accounts, checks their resulting roles,
staff sessions, Admin-only access, timestamps and lockout/mismatched-role
states. This tests the software contract; it does **not** prove the users'
actual private passwords on your live Docker database.

The last-successful-login timestamps in the real Nightjar Staff Access
screen provide the human acceptance evidence for the real accounts.
