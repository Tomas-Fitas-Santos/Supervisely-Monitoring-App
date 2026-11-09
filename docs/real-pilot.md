# Validate an event with real accounts

Use the application normally: run `Start-App.ps1`, sign in as the event admin,
and follow Event setup. There is no separate rehearsal or demonstration version.
Use the intended roster, source dataset and rules for the event being configured.

1. Verify the existing team and workspace IDs.
2. Upload the CSV and confirm that the monitor and participant lists match it.
3. Invite one person, a group or everyone. Confirm the native roles and acceptance
   in Supervisely, then refresh invitation status in the application.
4. Have a registered monitor sign in with their own User ID and personal token.
   Verify their assigned teams and that setup/reset controls remain admin-only.
5. Validate dataset staging, preview and explicit upload, then job preparation.
6. Check native job ownership, submission, correction return, reviewer handover
   and scoring against real accounts before relying on them for the event.
7. Review reset previews before confirming changes. Retained native resources
   and local backup records allow previous work to be inspected.

Shared native sequential-job integration remains incomplete; a prepared
application-level job is not proof of a published Supervisely labeling job.

For shared deployment, use [the hosted setup](production.md). The deployment
operator supplies PostgreSQL and, when needed, a polling credential. Each user
has their own authenticated session and monitoring assignments.
