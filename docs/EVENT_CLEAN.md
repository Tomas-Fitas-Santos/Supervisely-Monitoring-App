# Clean an event while keeping its datasets

The organiser-only **Settings → Clean event — keep datasets and registrations**
action resets rehearsal activity in the selected event. Preview its scope,
type `CLEAN <event name>`, then confirm. Implementing or deploying this feature
does not perform a clean automatically.

## Kept

- Current participant and monitor roster, team memberships, active flags,
  verified Supervisely memberships, monitor assignments and staff sign-ins.
- All ZIP, folder and mounted datasets; source files, inventory, classification,
  prepared queues, incremental import receipts and mirrored image IDs.
- Supervisely projects, datasets and source images, together with Nightjar's
  successful dataset indexing/mirroring operations.
- Event identity, schedule, integration settings and audit history.

## Cleared

- All video and image plans, assignments, queue blocks and work packets.
- In-progress, completed and correction labeling jobs in this event's owned
  Supervisely projects. Other events and unrelated projects are untouched.
- Annotations on managed source images and job copies, downloaded annotation
  caches, reviews, scores, rankings, closeout records and the generated export.
- Pizza/drink and other ticket allocations/redemptions, activity definitions,
  results and prizes, attendance and table assignments. Default empty ticket
  categories are recreated; no ticket balances or claims remain.

The roster is kept as it exists at cleanup time, rather than restored to an
earlier historical roster. Participants must check in again. Organisers can
create new plans against the retained datasets without uploading images again.

## Supervisely cleanup and failures

Stop participants annotating before confirming. Do not manually delete projects,
datasets or images in Supervisely. The clean action coordinates remote job
removal and local state cleanup; separate manual job deletion is unnecessary.
Remote job removal alone is not treated as annotation removal: the worker also
uploads empty annotation JSON, grouped by remote dataset, in batches of at most
200 images. It never uploads image bytes or replaces source IDs.

The request requires idle, ready datasets and completed synchronization and
mirroring. It locks the event against new work. PostgreSQL advisory locks let
in-flight publishing, validation, status and correction cycles finish before
cleanup, and prevent workers from writing during cleanup. Existing worker reset
handling temporarily pauses native processing across events while a reset is
pending; unrelated event records and remote work are preserved.

The recovery worker verifies ownership, removes jobs, clears annotations with
persistent batch checkpoints, and only then deletes local operational records
in a database transaction. Keep the API and workers running. Use **Refresh clean
status** to see progress. An error leaves the event locked and its local records
available; **Retry cleanup** resumes it. A process crash releases the advisory
lock and a replacement recovery worker can resume the saved operation.

Remote deletion cannot be rolled back. A failed cleanup can already have
removed some jobs or annotations, so retry to finish instead of reopening the
event for participants. Keep normal database backups before rehearsals; a
database backup does not restore deleted remote annotations.

The legacy dataset-deleting full reset remains a separate API mode for backward
compatibility. The Settings clean button explicitly requests dataset preservation
and uses a different confirmation phrase; it never selects that legacy mode.

## Verification

Disposable-data tests cover ZIP and mounted dataset preservation, both video
queue modes and linked image plans, completed reviews and rankings, in-progress
and correction jobs, ticket and activity cleanup, other-event isolation,
recreation of plans without image uploads, lost remote acknowledgements,
checkpointed annotation batches and worker restart. PostgreSQL tests exercise
worker/reset exclusion and deletion with real foreign keys.

Remote writes in tests use a simulated Supervisely SDK. No test cleans the
Official Event or performs real Supervisely deletions.

On 2026-11-13, all 354 backend tests passed against disposable SQLite fixtures
and migrated PostgreSQL, including the 50,447-frame sequence handoff. Ruff and
the production TypeScript/Vite build also passed. This verification did not
deploy the clean feature or execute it on the Official Event.

## Live deployment — 2026-11-13

Application commit `94298b5` was subsequently deployed with the user's approval.
The public tunnel passed readiness and served the new clean interface; the
authenticated Settings preview showed dataset preservation and left execution
disabled until the confirmation phrase is entered. All 14 native workers resumed.
No clean/reset operation was executed.

Before deployment, a fresh full database backup was restored into an isolated
PostgreSQL container and the Official Event's checksums matched. An independent
copy of all 50,447 original files (5,530,757,688 bytes) was made outside app media
storage; every copied file's SHA-256 matched its original. The original connection
and encryption configuration was also backed up privately.

After deployment, the Official Event's dataset, 50,447 assets/source mappings,
existing plan and 1,682 blocks retained their exact checksums. All 259 roster
records and 126 teams remained; 100 participants' verification/update timestamps
refreshed during live operation, with no other participant fields changed.
The verified local recovery copy is on the same computer, and does not back up
Supervisely's remote annotation/job state or protect against loss of that disk.
