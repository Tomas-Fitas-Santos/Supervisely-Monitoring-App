# Supervisely consistency check — 13 November 2026

Fixes deployed in release `20261113-190050`, completed at 19:02 UTC.

## Findings and repairs

- Eight historical jobs existed in Supervisely but Nightjar showed `publishing` with `native_job_confirmation_failed`. Supervisely had added name-collision suffixes such as `(#2)` and `(#3)`. Publication now recognizes these suffixes after checking the exact dataset, assignee and image identities. Regression tests cover both successful creation and a lost response, and reject mismatched images.
- Three cached native statuses still said `on_review` after Supervisely had changed them to `stopped`. Review/correction/completed jobs now receive a bounded status refresh independently of Nightjar's review decisions. Updates compare the native ID to avoid overwriting a replacement correction binding.
- The job screen now labels the local workflow as “Nightjar state” and shows when its Supervisely status was last checked. The review screen describes these as synchronized statuses.

The eight historical links were repaired using read-only Supervisely calls and audited local database transactions. Recovery created no remote jobs or image copies. Exact 30-image membership and assignees were verified for native jobs 194483–194486 and 194488–194491. The affected events were `teste luis v2` and `teste luis v3`.

## Final verification

All **53 current bindings in active events** matched their Supervisely job statuses and assignees in the final comparison. No binding mismatches remained.

| Official Event dataset | Nightjar source images | Supervisely source images |
| --- | ---: | ---: |
| Official Annotatiathon Dataset | 50,447 | 50,447 |
| NABirds | 48,562 | 48,562 |

Both source mirrors completed. The Official Event still had zero jobs and was not launched by this work.

Some historical Supervisely project totals include additional images in the manual-assignment dataset. These are independent annotation copies, so a whole-project image total can exceed Nightjar's source-library total. Correction jobs also remain separate native jobs while belonging to one logical Nightjar assignment. These differences were not treated as missing source images or duplicate assignments.

Four pre-existing historical `restart_job` operations remain in `attention`. Their cached native statuses now reflect Supervisely; this consistency repair did not retry those separate correction operations.

## Validation and recovery

- Focused publication/status regression run: **40 passed**.
- Full suite: **392 passed, 16 skipped**, zero failures. Skips: 12 requiring disposable PostgreSQL, four platform/filesystem-specific. The prior final-flow test run separately exercised the PostgreSQL suite.
- Frontend TypeScript/production build passed. Public domain served the new asset `index-CcAnyEpy.js` and readiness returned `{"status":"ok"}`.
- API/database healthy; all 14 native workers and the source indexer running after deployment.
- Pre-deployment database backup: [nightjar-20261113-190128.dump](../backups/nightjar-20261113-190128.dump).
- Test results: [JUnit XML](../backups/native-consistency-tests-20261113.xml). Backup artifacts are local and ignored by Git.

Implementation: [native_worker.py](../src/annotatiathon/native_worker.py), [native_status_cache.py](../src/annotatiathon/native_status_cache.py), [regression tests](../tests/test_native_reconciliation.py).

## Email identity reconciliation — deployed later the same day

The registration mismatch was caused by username matching taking precedence over email. Supervisely permits distinct usernames differing only in case, while Nightjar previously folded their case. A separate stale native account ID also blocked the automatic refresh.

- Matching now uses an existing verified native ID, then a unique email normalized for surrounding whitespace and case. A username fallback requires exact case and an unavailable native email. Ambiguous emails and conflicting verified IDs require review.
- The Official Event gained **12 new identity links**. One additional stale link was explicitly replaced with the unique current member matching its email, after confirming the old ID was absent from the team and had no jobs, native job history, other roster links, or staff-account links. The replacement has a dedicated audit record.
- Final live comparison: **177 current Supervisely members, 177 linked roster people, zero email/login/membership mismatches, zero conflicts**. The active roster contains 261 people; the remaining 84 have no matching current team membership. Registrations continued during deployment, so the native-member count increased from the initial 174.
- No people were created during this reconciliation. Names, emails, roles in Nightjar, teams, check-ins, tables, and registration source fields were preserved. A second refresh updated zero records. Supervisely was read only; the Official Event still had zero jobs.
- Membership, identity, monitor-registration, and roster-removal tests: **37 passed**. Frontend TypeScript/production build passed. The regression cases cover case-distinct usernames, email normalization, ambiguous emails, stale verified IDs, and exact-login fallback.
- Public domain returned HTTP 200 and served `index-Dpgj-mVu.js`; readiness returned `{"status":"ok"}`. API/database healthy, indexer and all 14 native workers running after restart.
- Pre-deployment backup: [nightjar-20261113-191626.dump](../backups/nightjar-20261113-191626.dump).

Implementation: [membership matching](../src/annotatiathon/native_sync_api.py), [regression tests](../tests/test_native_sync.py), [membership UI](../web/src/native.tsx).
