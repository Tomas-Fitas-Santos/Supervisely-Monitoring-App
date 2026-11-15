# Final flow validation — 13 November 2026

The deployed job flow passed a real Supervisely rehearsal with two verified monitor accounts. The full automated suite finished with **397 passed, 0 failed, 4 skipped**. The Official Event was not launched or used for test assignments.

## Real Supervisely rehearsal

The rehearsal used deployed application modules, a separate disposable PostgreSQL database, a separate event, and a new Supervisely project. It copied 65 frames from the official source dataset. Production database connections were explicitly transaction-read-only. Monitor accounts 123550 and 124114 were used; their real roster records and native roles were preserved.

| Step | Verified result |
| --- | --- |
| Plan creation and approval | Shared 30-frame plan created and approved; launch before replication rejected with HTTP 409. |
| Source replication | All 65 copied frames uploaded to a separate project. |
| Distribution | Two distinct 30-frame jobs assigned to two monitors. A duplicate claim by an occupied annotator returned no job. |
| Partial submission | 29 complete frames accepted; one frame missing landmarks required correction. The annotator's packet stayed open. |
| Monitor review | Review claimed and completed; repeating completion did not double count. Ranking recorded one reviewed frame, one correct box, and four correct landmarks. |
| Correction | A new native job contained exactly the one incomplete frame. Correcting it brought accepted snapshots to 30. Previous accepted snapshots and the completed review survived. |
| Queue advancement | Completion released the final five-frame job. Three primary jobs covered 65 distinct frames, with no duplicate source upload. |
| Cleanup | Jobs removed, projects archived, container-side image copies deleted, disposable database container removed. |

Successful project: 386819. Initial jobs: 194545 and 194546; correction: 194547; tail: 194548. A first rehearsal stopped at a script assertion using an unavailable SDK response field. Its project 386818 and jobs 194542–194544 were also cleaned up. The assertion was corrected to use returned job entities; application code needed no change.

Independent checks confirmed all seven job IDs return no accessible job and neither test project appears in the active workspace. Supervisely may retain archived projects and audit history; cleanup does not erase provider history. Automatic command review blocked recursive removal of the local Windows QA temporary folder; this does not affect the application or remote cleanup.

## Automated coverage

`pytest tests -q` ran against a fresh, migrated PostgreSQL 17 test database where PostgreSQL-specific tests required one. Other tests used existing isolated fixtures. Duration: 137 seconds.

- Folder, ZIP, mounted-folder and mounted-sync imports across manual, shared and balanced distribution: 12 full lifecycle combinations.
- Membership eligibility, check-in gates, monitor pair assignment and role permissions.
- Concurrent claims, duplicate-person requests, concurrent validators and worker interruption recovery.
- Partial submissions, corrections, immutable accepted snapshots, review leases and scoring idempotency.
- Sequential video-to-image plans, scale contracts, event cleanup with real foreign keys, and reset/worker exclusion locks.

Four platform-specific skips: two Linux sparse-file fixtures, Windows symlink support, and Linux directory-handle behavior. These are not claimed as passed. Dependency deprecation warnings were also reported.

One pagination fixture in [test_native_sync.py](../tests/test_native_sync.py) matched raw JSON spacing. It now parses JSON before checking the page number. A rerun against the already-used test database encountered three fixed-name fixture collisions; the final run used a fresh disposable database and passed. No production behavior was changed for these tests.

Machine-readable results: [JUnit XML](../backups/final-flow-tests-20261113.xml). Backups are local ignored artifacts and may not be present in another checkout.

## Production state after cleanup

Checked at approximately 18:02 UTC:

- Public domain returned HTTP 200; `/health/ready` returned `{"status":"ok"}`.
- API and database containers healthy. Web, indexer, four publishers, three validators, three correction workers, status, recovery, bulk and mirror workers running.
- Official Event `ff71f54f-9f77-45c8-8504-c9c4028b0249` still had **zero jobs**.
- Plan `e12d250e-1605-411e-a51d-b0edd2d5310b` remained **planned**.
- Official Annotatiathon Dataset fully mirrored: **50,447 / 50,447**, ready for automatic jobs.
- NABirds still uploading: **18,432 / 48,562**, no current mirror error; not yet ready for automatic jobs.
- Real participant check-ins and roster activity continued during testing and were preserved. No whole-database restore was performed.
- Host C: drive had about **21 GiB free (2.3%)**, an existing storage-health concern.

The live rehearsal exercised deployed service logic and real Supervisely writes, using administrator-submitted synthetic annotations on disposable frame copies. It did not test a human monitor's browser session or the labeler's manual editing experience. HTTP permission and workflow contracts were covered by the automated suite; public domain readiness was checked separately.
