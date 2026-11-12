# Nightjar — implementation audit and final event decisions

**Date:** 12 November 2026  
**Branch:** `main` (single working branch)  
**Purpose:** distinguish implemented code from fully verified event operation. This audit does not authorise a live database reset or automated creation of native jobs.

## Implemented in this audit

1. **Event-wide schedule** (`0021_event_schedule`): additive nullable UTC start/end columns, PostgreSQL check constraint, organiser-only `PUT /api/v1/events/{event_id}/schedule`, audit trail, current event view, and validation of newly created/edited activities against event bounds. Existing events with no schedule are compatible; existing scheduled activities block shrinking the window if they would fall outside it. The React settings and activities pages use `Europe/Lisbon` and reject ambiguous/nonexistent local DST times.
2. **Every submitted frame enters the shared review backlog** (`0022_full_frame_review`): `ReviewSample` identity is now (job, correction round, source media frame). It includes single-assignee manual, 30-frame primary, and repeat jobs, rather than selecting one sample from a primary job. The original annotation JSON is retained, not replaced with images. Existing sampled review rows and decisions survive the additive change, and idempotent ingest fills missing frames when an already-saved manifest is encountered. Existing historic two-assignee jobs remain on their legacy review API.
3. **Organiser-issued monitor registration codes:** existing `registration_challenges` table now holds one hashed, single-use code per approved monitor, expiring after seven days. Only organisers can create codes. Unknown roster addresses and already-existing Nightjar accounts cannot receive them. Five incorrect attempts lock the code; the organiser may generate a new one. The Staff Access page shows a generated code once; the monitor enters it during Nightjar registration. Existing Nightjar staff accounts, sessions and passwords are unchanged. No email/Supervisely API token is sent to the browser.
4. **Supervisely rectangle opacity default:** SDK worker checks the *actual project settings* for a known rectangle-opacity key, updates only its value to zero while preserving other settings, and confirms the result. If the field is unavailable or unsupported, worker logs a warning and continues publishing; **the default is not guaranteed** without a live project-settings read-back. It does not enforce an individual labeler's personal preference.
5. **Automated CI:** PostgreSQL queue-concurrency tests are enabled in the standard workflow rather than skipped. Existing FastAPI, SDK-worker image, migrations, frontend compile and disposable application-stack checks are retained.

## Important architectural invariants

- Do not drop or recreate database volumes; the migration is additive and must run before the updated API/worker.
- There are two **different queues**: outbound labeling jobs and inbound frame-review claims. They have independent lifecycle states and do not require monitor-to-team assignment.
- The inbound review queue uses team-first fairness and random frame selection. It guarantees *exclusive active claims* under PostgreSQL row locking, but a backlog may contain far more frames than monitors can review during the event.
- The leaderboard shows **correctly reviewed original keypoints + boxes**, not all unreviewed submitted objects. Only round zero is currently included in primary rankings; correction policy requires an explicit decision.
- The automatic default **`shared_30_v1`** creates up to 30-image jobs. Older **`legacy`** mode creates whole-video jobs. The organiser must decide which policy applies to the official event; preserve both until then.
- Manual jobs and repeats maintain independent annotation snapshots. Do not fetch image bytes with submissions when the local original is retained.
- Role edits in Nightjar are separate from the actual Supervisely team role; linked native team IDs must be confirmed before assigning jobs.
- The worker may issue the next assignment after native submission while review grading happens asynchronously. Do not silently couple those queues without an organiser decision.

## Operational issues and checks (production gate)

| Area | Potential failure | Gate |
| --- | --- | --- |
| Identity | Native member email unavailable or joined account not mapped to numeric Supervisely user ID | Refresh membership; review unmatched members; create jobs only for verified, eligible IDs |
| Signup | Approved roster email claimed by the wrong person | Deliver the single-use organiser code over a trusted private channel; confirm an invited monitor can register and an uninvited person cannot |
| Authentication | Expired/locked Nightjar password or wrong event selected | Sign in all staff and verify event-specific permissions and lockout recovery |
| Dataset | Imported video has gaps/unknown frame density; huge full-density dataset exhausts media storage | Check provenance, exact frame count, source IDs, manifest, free disk and backups |
| Queue | Manual priority starves automatic work; worker restart duplicates remote operations | Trial high/normal/held jobs, restart worker mid-operation, verify idempotent remote ID reconciliation |
| Publication | Job issued to wrong native user or source video mixed unexpectedly | Verify native job owner IDs, source videos and repeat-batch rules with two distinct users |
| Submission | Supervisely job status transitions to review but frame JSON unavailable or out of order | Check all source-media IDs have matching job-scoped annotation JSON; retry without duplicate snapshots |
| Review | Two monitors claim same frame, lease expires, tab crashes, release fails | Run real PostgreSQL concurrent tests and browser claim/pause/crash/recovery scenarios |
| Fairness | Some teams submit much more; pure total counts favor available/fast teams | Publish per-team reviewed-frame counts, compare equal review coverage and settle scoring finalisation rule |
| Scoring | Reviewer's class correction counted as participant accuracy; correction round double-counted | Preserve original verdict and versioned rubric; decide correction-round winner policy |
| Timing | Per-image active annotation duration not exposed by native SDK | Show “unavailable” rather than a fabricated duration; report only verified native statistics |
| Event | Activities scheduled outside event dates or at ambiguous Lisbon DST transitions | Test organiser create/edit/move-window, archive, and different local timezones |
| Role safety | Monitors discover organiser-only routes by directly calling the API | 403 on roster import, role edits, resets, destructive actions and scoring-rule changes |
| Closeout | Event archived with unfinished native jobs, missing review coverage, or incomplete export | Agree acceptance gate before final export; backup and test restore; do not force-clear history |
| Hosting | Cloudflare Quick Tunnel disconnects mid-event; local PC sleeps; Wi-Fi saturation | Use stable HTTPS, device uptime, backups, disk monitoring and full-event stress rehearsal |
| UI | Internal roster scroll regresses or reviewer zoom is slow at full resolution | Test 1920×1080 and laptop widths; verify full-width frame and annotation selection |

## Required organiser decisions

**A. Automatic job format.** Official event uses (1) **up-to-30-frame jobs**, current `shared_30_v1` default, including independent repeat batches, or (2) **one whole video per job**, legacy mode? If option (1), are cross-video repeat batches acceptable, or must each native job remain from a single video?

**B. Final leaderboard fairness.** Only reviewed frames earn correct-object points. Do we (1) enforce an equal reviewed-frame quota per team before declaring a winner, or (2) allow scores to accumulate continuously with the current team-balanced claim order? If groups differ in number of visible birds/objects, should we show raw correct-object counts as primary rank (as previously instructed), plus accuracy/coverage as diagnostics?

**C. Correction rounds.** When a participant re-submits a previously reviewed image, should the **original first submission alone** count for the competition, or should the latest corrected submission replace its prior score (without double counting)? Current leaderboard uses round zero.

**D. Next-job release.** Current shared automatic queue can release the next job once the previous native submission is completed, before the independent frame reviews finish. Confirm this is intended.

**E. Bounding-box Visuals settings.** A sanitised output from `api.project.get_settings(project_id)` (no credentials/tokens) or the exact option key is needed to ensure the zero-fill default is applied by the installed Supervisely SDK version.

## Deployment and validation sequence

1. Announce a short deployment freeze to both developers; confirm `main` CI green and no ongoing commits. Do **not** create another branch.
2. Back up live PostgreSQL, media directory, encryption key and credentials in a private location; verify restore method.
3. `git pull --ff-only origin main` on the deployment host. Inspect pending migrations and changed native-worker code.
4. `docker compose --env-file .env.live -f compose.live.yaml up -d --build api web`; migration service runs before the API. Verify `/health/ready`.
5. Only when the organiser has confirmed the exact active plan and team, start/rebuild the native worker: `docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --build worker`. Never launch test native jobs into official event data accidentally.
6. Live acceptance: two participants join/refresh → publish distinct jobs → submit frame JSON → two monitors claim different frames → complete/pause → rank score updates exactly once; redo after worker restart and browser crash.
7. Run a full-scale capacity rehearsal with the **final** agreed queue policy before opening to all participants. Prior simulated benchmarks showed throughput limits; tests with synthetic latency are not equivalent to real Supervisely capacity.

**No claim of production readiness is valid from unit tests alone.**
