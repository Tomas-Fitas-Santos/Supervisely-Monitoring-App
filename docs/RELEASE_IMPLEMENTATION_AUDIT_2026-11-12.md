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
- The inbound review queue draws uniformly from the *entire* current-round eligible submitted-frame pool (random-key pivot), not from the least-reviewed teams. Higher-volume teams contribute more frames and have proportionally more opportunities to earn points. PostgreSQL row locking and exclusive reviewer leases prevent simultaneous claims; monitors can complete-and-next, complete-and-pause, or resume. The backlog need not be entirely reviewed before winner determination.
- The official leaderboard ranks by **total weighted verified annotation points**, not by correctly annotated images, ratios, or equal reviewed-frame quotas. Each correct visible keypoint defaults to +1, each correct occluded keypoint (+ `_occ` class) to +3, and each correct bird bounding box to +1. Incorrect types have individually configurable subtraction weights (initial defaults: wrong class 1, wrong placement 2, sabotage 3; the organiser can change these). The latest organiser-published rubric is applied uniformly to immutable current-round review verdicts, without rewriting historical decisions. Raw correct annotation totals remain visible for transparency.
- The official **`shared_30_v1`** automatic mode allocates one continuous dataset-wide sequence of frames: jobs contain 30 frames, including video-boundary transitions when necessary. Only the final dataset job may be shorter. Older **`legacy`** mode remains available for historical whole-video plans; manual segment selection remains single-video.
- Manual jobs and repeats maintain independent annotation snapshots. Do not fetch image bytes with submissions when the local original is retained.
- Role edits in Nightjar are separate from the actual Supervisely team role; linked native team IDs must be confirmed before assigning jobs.
- The worker may issue the next assignment after native submission while review grading happens asynchronously. Do not silently couple those queues without an organiser decision.

## Operational issues and checks (production gate)

| Area | Potential failure | Gate |
| --- | --- | --- |
| Identity | Native member email unavailable or joined account not mapped to numeric Supervisely user ID | Refresh membership; review unmatched members; create jobs only for verified, eligible IDs |
| Signup | Approved roster email claimed by the wrong person | Deliver the single-use organiser code over a trusted private channel; confirm an invited monitor can register and an uninvited person cannot |
| Authentication | Expired/locked Nightjar password or wrong event selected | Sign in all staff and verify event-specific permissions and lockout recovery |
| Dataset | Imported video has gaps/unknown frame density; huge full-density dataset exhausts media storage | No JSON required: inspect inferred numeric suffixes or natural frame order, source identity and gaps, free disk and backups; sparse frame folders remain ineligible for full-density automatic queues |
| Queue | Manual priority starves automatic work; worker restart duplicates remote operations | Trial high/normal/held jobs, restart worker mid-operation, verify idempotent remote ID reconciliation |
| Publication | Job issued to wrong native user or mixed-video sequence misattributed | Verify native job owner IDs, original per-frame video identity, frame order, and that repeat blocks are not auto-issued |
| Submission | Supervisely job status transitions to review but frame JSON unavailable or out of order | Check all source-media IDs have matching job-scoped annotation JSON; retry without duplicate snapshots |
| Review | Two monitors claim same frame, lease expires, tab crashes, release fails | Run real PostgreSQL concurrent tests and browser claim/pause/crash/recovery scenarios |
| Fairness | Some teams submit much more; more correctly annotated objects should earn more points | Use independent random sampling from the full eligible frame pool, expose coverage per team, and apply the same current published rubric to every completed review |
| Scoring | Reviewer's class correction counted as participant accuracy; correction round double-counted | Preserve original verdict and versioned rubric; decide correction-round winner policy |
| Timing | Per-image active annotation duration not exposed by native SDK | Show “unavailable” rather than a fabricated duration; report only verified native statistics |
| Event | Activities scheduled outside event dates or at ambiguous Lisbon DST transitions | Test organiser create/edit/move-window, archive, and different local timezones |
| Role safety | Monitors discover organiser-only routes by directly calling the API | 403 on roster import, role edits, resets, destructive actions and scoring-rule changes |
| Closeout | Event blocked by unfinished participant jobs or finalized before complete submissions have annotation JSON | Unsubmitted/returned incomplete jobs earn zero and do not block; completed native submissions must have all current-round JSON frames mirrored, and at least one frame review must be verified |
| Hosting | Cloudflare Quick Tunnel disconnects mid-event; local PC sleeps; Wi-Fi saturation | Use stable HTTPS, device uptime, backups, disk monitoring and full-event stress rehearsal |
| UI | Internal roster scroll regresses or reviewer zoom is slow at full resolution | Test 1920×1080 and laptop widths; verify full-width frame and annotation selection |

## Official competition rules — confirmed by organiser on 12 November

1. **Distribution (final updated decision):** automatically allocate videos in deterministic source order as one continuous frame stream. A native job contains 30 frames in order, except the final dataset-wide job, which may be shorter. If Video A ends with 14 remaining frames, the same job continues with Video B frames 1–16. Each source segment retains its native video identity, original ordinal, species, morphology and independent annotation provenance. Primary frames are allocated exactly once: no drops, padding or duplication. The PostgreSQL plan lock serializes concurrent claims, and the planner stores a globally increasing block ordinal. Manual job selection still uses 30 consecutive frames from *one* video.
2. **Structural completeness on each submitted image (12 November updated):** after Supervisely moves a single-annotator job to submitted/on-review, the worker retrieves job-scoped **annotation JSON only** and checks each frame for **exactly one bird rectangle + exactly one left-eye, right-eye, beak and crown point** (each point may be visible or `_occ`). This checks existence and shape only; monitors judge correctness and placement. Complete frames are immediately snapshotted into the exclusive random review queue, with persistent source-media IDs. Remaining incomplete images are issued back **only to their original annotator** as a new Supervisely job with an `(Incomplete #N)` suffix.
3. **Single logical Nightjar job across corrections:** repeat submissions share one original Nightjar job ID and WorkPacket, even though remote Supervisely has distinct job IDs. Accepted images enter review **once** across all correction rounds, remain in scoring while later images are fixed, and are never included in a later correction subset. Native job history remains audit-visible. A new automatic assignment is held until all images from this original logical job are structurally complete. The correction worker uses supported native job creation with explicit incomplete image IDs; it avoids the SDK restart path that triggers an unsupported `users.info` lookup. An old `attention` operation can be retried by the organiser; the worker first recovers valid annotations from its original submission.
4. **Current prize scoring (Luis update):** a reviewed image earns up to five anatomical points: correct bird rectangle, left eye, right eye, beak, and crown (one each; visible/occluded variants equivalent). The leaderboard estimates a team's score as **eligible submitted primary frames × average observed five-point review score**; independently verified correct points accumulate as monitor reviews complete. The old editable weighted rubric is now a historical/secondary quality metric and does **not** determine prize ranks; its obsolete UI tab was removed. Review allocation is approximately proportional to submitted-frame shares, with a random available frame within the selected team. No equal reviewed-frame quota; more complete submissions produce more scoring opportunities.
5. **Next assignment:** only all structurally accepted images release the annotator's outbound WorkPacket for the next job. Partially accepted jobs keep the slot reserved while the accepted subset is available to monitors asynchronously.

**Dataset edge case resolved:** the planner carries each video's last 1–29 frames into the next video and preserves each frame's original source metadata. Only the final job of the *entire dataset* may contain fewer than 30 frames; that job uses its actual expected frame count for submission-completeness validation. A 74-frame Video A followed by Video B produces jobs A1–30, A31–60, A61–74 + B1–16, B17–46, and so on.

**Historical compatibility:** pre-existing whole-video labeling jobs and old review records remain readable. The new equal-coverage closeout gate applies when the event contains official new single-assignee shared/manual frame-review jobs. The older accepted-job review workflow remains compatible with historical events.

**Official distribution safety default:** the worker does not automatically schedule repeat copies of 30-frame blocks. All primary frames are allocated once. The optional repeat-specific functions remain in the source code for future explicit use, but normal event operation does not invoke them; the short final block is not repeated.

## Deployment and validation sequence

1. Announce a short deployment freeze to both developers; confirm `main` CI green and no ongoing commits. Do **not** create another branch.
2. Back up live PostgreSQL, media directory, encryption key and credentials in a private location; verify restore method.
3. `git pull --ff-only origin main` on the deployment host. Inspect pending migrations and changed native-worker code.
4. `docker compose --env-file .env.live -f compose.live.yaml up -d --build api web`; migration service runs before the API. Verify `/health/ready`.
5. After migrations and **while the native worker is stopped**, backfill any pre-upgrade completed submissions already captured as JSON. This does not contact Supervisely, download images, or overwrite completed reviews:
   ```powershell
   docker compose --env-file .env.live -f compose.live.yaml exec -T api /usr/local/bin/nightjar-entrypoint python -m annotatiathon.manage backfill-frame-reviews
   ```
   Inspect the reported `frames_queued`, `missing_snapshots` and `failed_jobs` counts; missing/failed jobs require reconciliation before final competition ranking. Re-running the command is safe.
6. Only when the organiser has confirmed the exact active plan and team, start/rebuild the native worker: `docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --build worker`. Never launch test native jobs into official event data accidentally.
7. Live acceptance: two participants join/refresh → publish distinct jobs → submit frame JSON → two monitors claim different frames → complete/pause → rank score updates exactly once; redo after worker restart and browser crash.
8. Run a full-scale capacity rehearsal with the **final** agreed queue policy before opening to all participants. Prior simulated benchmarks showed throughput limits; tests with synthetic latency are not equivalent to real Supervisely capacity.

**No claim of production readiness is valid from unit tests alone.**
