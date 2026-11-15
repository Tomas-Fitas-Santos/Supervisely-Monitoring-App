# Event continuity and recovery

Committed event progress lives in PostgreSQL, not in a browser or worker's
memory. Restarting the application must reuse the existing database volume,
media mounts and encryption key. **Never use Clean event, a reset endpoint,
or `docker compose down -v` to recover from an outage.**

## Failure handling

| Failure | Preserved state and recovery |
| --- | --- |
| Browser refresh, lost tunnel or connection | Saved plans, assignments, submissions and review decisions remain in the database. Reconnect to the same event. Unsaved browser edits and annotations not saved by Supervisely are outside this guarantee. |
| Worker process killed during job publication, correction or reassignment | Its committed assignment remains reserved. The recovery worker checks a PostgreSQL session lock, not elapsed time. Once ownership is gone, it flags the operation for reconciliation in Job Distribution. |
| Remote write succeeded but its response was lost | Use **Job Distribution → Supervisely synchronization → Retry**. Existing deterministic names and native IDs reconcile the remote result. Never manually recreate the job or requeue its frames. Ambiguous identities fail closed for inspection. |
| Worker killed during submission validation | Renewable 120-second leases allow the recovery worker to reclaim the read task. Stale owners cannot finalize a newer lease. Retries are bounded; repeated failures become visible for organiser action. |
| Worker/database temporarily disconnected | Worker polling pauses and retries. Publication bindings and task completion commit together; reassignment ownership and task completion also commit together. No partially committed local ownership change is exposed. |
| Monitor browser closes during a review | The existing review lease expires and the frame becomes claimable again. Committed review decisions/scores remain; incomplete unsaved edits may need re-entry. |
| Crash between video and image plans | Both plans and their dependency are durable. Transactional activation is repeatable, and concurrent claim tests verify distinct packets. The handoff is still after the last video assignment, not after all reviews. |
| Crash during import/mirroring | Existing staging/source receipts and operation locks support resumption. Mirroring inventories remote image identities before retrying. Keep mounted originals and uploaded source storage available. |
| Host reboot | Docker services use `restart: unless-stopped`; Docker itself and the tunnel must be started. Persisted volumes/mounts must be the same. Restarting a tunnel does not recover lost storage. |
| Disk loss or database corruption | Requires an independently stored, tested database/configuration backup plus source media. Same-disk copies do not protect against losing that disk. Remote Supervisely data is a separate system. |

Uncertain **write** operations are deliberately not automatically replayed.
Other eligible participants can continue, but the affected assignment remains
reserved until reconciliation. A hung live SDK call retains its operation lock;
recovery does not steal it because it is slow. An operator may need to restart
the affected worker after diagnosing the service/network failure.

Recovery runs without requiring a working Supervisely connection. Old attention
operations sort ahead of completed history in the UI, and status counts include
the entire event rather than just the latest 1,000 operations.

## Deploying the ownership protocol

No schema migration or dataset re-indexing is needed. Upgrade the API, UI, all
publishers, correction workers and recovery worker together, using the existing
deployment script and its pre-upgrade backup. Do not run mixed versions of the
writers deliberately.

Only writes claimed by the new session-lock protocol are automatically detected
as abandoned. Pre-upgrade `running` writes have no ownership marker and are left
alone: absence of a new lock cannot prove that an old worker stopped. Check those
operations explicitly before upgrading; do not bulk-clear or replay them.

## Online backups

From the repository root, a single snapshot without stopping the event:

```powershell
.\scripts\backup-live.ps1
```

For repeated snapshots while this PowerShell process remains running:

```powershell
.\scripts\backup-live.ps1 -Continuous -IntervalSeconds 300
```

Use `-Destination` with an existing private backup destination on a separate
disk when available. The script makes a consistent PostgreSQL dump, verifies
its archive structure, copies the original environment/encryption configuration,
records SHA-256 hashes, and publishes the snapshot directory only after success.
It retains prior snapshots and retries failed attempts in continuous mode.
Monitor free space: there is no automatic deletion/retention policy. This loop
is **not** installed as a Windows startup task or enabled by deployment.

Database snapshots contain event state and the database's immutable frame
annotation/review records. They do **not** copy uploaded media, mounted originals,
legacy annotation files, or external Supervisely state. Keep those media sources
backed up separately. The existing Official Event source copy contains the
original 50,447 uploaded frames; new datasets or later additions need their own
media backups. The environment copy is private and must never be committed or
shared publicly.

For ordinary process/reboot recovery, use the current database; do not restore
an older dump. For storage disaster recovery:

1. Stop the application writers, retaining all existing volumes and files.
2. Verify the backup manifest hashes and restore into a **new isolated database**
   first. `pg_restore --list` checks archive structure; only a successful restore
   and content verification establish a tested restore.
3. Restore the original encryption configuration and media paths. Validate
   dataset/asset IDs, native mappings, plans, jobs and reviews before cutover.
4. Reconcile native jobs created or updated since the snapshot before resuming
   writers. A database rollback cannot roll back Supervisely. Blindly resuming
   from an old dump can duplicate later work or lose later local decisions.

Scheduled snapshots are a recovery point, not zero-loss disaster protection.
Five-minute snapshots can lose changes since the most recent successful dump
if primary storage is destroyed. PostgreSQL's normal crash recovery preserves
committed transactions on surviving durable storage; loss of the host/disk needs
off-host backups or a separately designed replication/WAL-archiving setup.

## Verification in this change

Tests inject process death before remote creation, after remote creation and
before the local completion commit; retries preserve assignments and native IDs
without duplicating jobs. Correction and reassignment tests lose a successful
remote response and then finish the original lifecycle. A real child process
holding a PostgreSQL operation lock is forcibly killed in an isolated test:
recovery ignores it while alive and records exactly one interruption afterward,
even with concurrent recovery workers.

The regression suite also covers validation lease recovery, review ownership,
partial submissions/corrections, queue allocation and the synthetic 50,447-frame
video-to-image handoff. Remote calls use test doubles; no real participant jobs
are created by these tests.

Validation: **373 tests passed** against a fresh disposable PostgreSQL database
(including all PostgreSQL opt-in tests), repository lint passed, and the frontend
type check/production build passed. An earlier run reused the targeted-test
database and hit two fixture-name collisions; the new crash test now uses a
unique event name, and the full successful run used a new database.

On 2026-11-13 the live database was checked read-only: `fsync`,
`synchronous_commit` and `full_page_writes` were enabled. A fresh online snapshot
was restored successfully into an isolated PostgreSQL database, including
106,491 assets, 106,265 source mappings, 16 plans, 75 jobs and 340 review rows
across events. Existing attention operations belonged to older test events;
there were no running job-write operations at the time of the check.

These changes and the backup utility are prepared locally. This verification
does not itself deploy them, enable continuous backups, or establish off-host
disaster recovery. It does not certify every possible external-service failure.
