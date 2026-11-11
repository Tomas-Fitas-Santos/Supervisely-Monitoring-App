# Nightjar — Two-developer event implementation plan

Status: **Approved planning baseline, not a declaration of implemented features**  
Date: 2026-11-11  
Branch policy: **both developers implement on `main` after integrating `feature/event-queue-and-benchmarks`**. No new feature branches or demo backend.  
Developers: **Tomas (event operations, frontend, access, check-in, schedule)** and **Luís (Supervisely integration, both queues, review API, scoring, rankings, database schema)**.

## Product decisions (supersede incompatible older proposals)

1. **Navigation.** Event-day modules are visible to every authorised Nightjar monitor and organiser: overview, attendance/check-in, tickets, activities, job status, shared frame review, and results/rankings. Pre-event setup modules (roster import/invitation, team creation, dataset import, manual/automatic job creation, staff access and credential/configuration changes) are visible only to organisers. A scoring-rubric read-only summary is available to monitors; only organisers may edit rules. Enforce every role decision on backend endpoints, not merely through hidden buttons. Supervisely roles do not automatically grant Nightjar organiser.
2. **Two distinct queues.** (A) The **outbound job queue** publishes organiser-created assignments, one whole video per individual labeling job, preserving manual priority/high/low and automatic planning. (B) The **inbound frame-review queue** contains one immutable annotation snapshot per **submitted source frame per annotator and correction round**, not entire jobs. No team-to-monitor assignment. Every monitor can claim the next unreviewed frame from any team.
3. **Fair review claims.** Atomic PostgreSQL claim under concurrency. First select an eligible team with the **smallest completed plus actively claimed review count** (among teams with ready frames), randomly break ties, then randomly select an unclaimed frame for that team; do not sort by submission/job sequence. Use indexed randomized keys instead of globally sorting large tables by random(). Distinct claims must not return the same frame to two monitors. Persist ownership, claim expiry/heartbeat, completion, and idempotent recovery. Provide **Complete & Next** and **Complete & Pause**; resume only on user command. No frame lost on a monitor browser crash.
4. **Review UI.** Show image filename, team name and annotator name; use an image-first full-width responsive canvas with pan/zoom, original local frame and rendered JSON geometry. Review **each individual bounding box and keypoint** with Correct/Incorrect selection, not aggregate numeric fields. For incorrect keypoints capture reason `wrong_class`, `wrong_placement`, or `sabotage`. For wrong class, require the reviewer-chosen correct class (metadata-only correction; preserve original annotation, never silently alter the Supervisely submission). For incorrect boxes keep an explicit reason classification if later defined, otherwise at least Incorrect. Track per item, image, user, team, morphology, reviewer, timestamps, and correction rounds. Record unannotated/missing items separately from incorrect placed items.
5. **Ranking.** Primary ordering is **total correctly annotated keypoints + total correctly annotated bounding boxes** for each team, not completed or accepted-frame counts. Organiser-configured scoring weights and penalties are versioned and shown as secondary score/tie-break information unless explicitly revised; original verdict counts remain authoritative. Show separate totals/accuracy, review coverage, mistakes by reason, and member/team views. Do not count a reviewer’s corrected label as an originally correct participant label. Unreviewed frames do not add points.
6. **Supervisely fidelity.** On join and refresh, verify team membership and capture user ID, login, role, and matching roster email. Distinguish invited, joined, and explicitly linked Nightjar staff credentials. Native worker persists job↔annotator↔frame IDs; fetch **annotation JSON only** on submission, reuse locally uploaded images, validate per-frame mapping and revisions, and retry failed snapshots without duplication. If Supervisely exposes **trustworthy per-frame annotation durations**, store/display them; otherwise display unavailable, not an estimate masquerading as native data.
7. **Check-in.** Only participants need check-in; exclude monitors from check-in list and attendance denominators. One click checks in when a team already has a table, without confirmation. If no table is assigned, use an **in-page dialog** requesting a number once, then record arrival. Display **Check out** after arrival, also one-click, reversible. On last checked-out team member, clear the team table assignment; never clear the table while another teammate remains checked in. Check-in/out is attendance only, **not** a destructive cancellation of active native jobs.
8. **No browser prompts.** Replace `window.alert`, `window.confirm`, and `window.prompt` with shared app dialogs, safe confirmation sheets for destructive operations, validation fields and inline toast/notice feedback. Do not use browser-native top-of-page dialogs for ordinary operations.
9. **Event schedule and layout.** Organiser sets event start/end **date and time** with timezone `Europe/Lisbon`; activities must be scheduled within that interval. Persist UTC instants and show local timezone in UI. Wide roster/check-in tables have one internal scroll viewport (sticky header, horizontal scrolling as needed), not duplicate document-plus-table vertical scrolling. Maintain responsive usability on event monitors’ laptops.
10. **Bounding-box fill.** Aim for default rectangle **fill opacity 0** with borders and keypoints visible. Project Visuals exposes Rectangle opacity; the SDK exposes `api.project.get_settings` and `update_settings`. Inspect the **actual instance/project settings JSON field** first, update using merge semantics to preserve unrelated settings, then test a fresh annotator account. Treat it as a project default, not a guaranteed override of personal UI state.

## Phase 0 — Integrate Luís's existing branch BEFORE implementation

Repository: `Tomas-Fitas-Santos/Supervisely-Monitoring-App`  
Feature ref: `feature/event-queue-and-benchmarks` (head at planning inspection: `ebac125ff6c085e21a8a59523a9f8fa21bf8fbc4`)  
Main head at inspection: `2a83e2fb34c2a4a5c4399a32da6e6b7cc6016c3e`  
Compare at inspection: **diverged: 2 ahead, 24 behind**; refresh before integrating.

**Integrate selectively and reconcile; do not fast-forward or force-push.** Useful feature code: `frame_extraction.py`, `frame_import.py`, `job_reads.py`, `snapshot_metrics.py`, pure `work_queue.py`, benchmarks and their tests/reports. Actual persisted outbound dynamic queue and shared inbound frame-review queue **do not yet exist**. The old feature plan requires team-assigned monitors and some sampled-only/30-frame primary work assumptions; those are now superseded.

**Hard conflicts/required resolution:**

- `main` already has Alembic revision `0016_preloaded_media_pool.py`, while the feature adds `0016_frame_identity.py` with the same parent `0015_single_assignee_jobs`. Rebase the feature's *revision identity and parent* into an additive `0017_frame_identity` after `0016_preloaded_media_pool` (or select the next free revision if main advances), check no duplicate operations, and run `alembic heads` (exactly one) and `upgrade head` on disposable copied data.
- Both branches alter `models.py`, `datasets_api.py`, `labeling_api.py`, `native_worker.py`, `reviews_api.py`, `review_dashboard_api.py`, `web/src/reviews.tsx` and the upload UI. Integrate latest main **Supervisely ID linking**, annotation snapshots, native media preload/manual assignment, and permissions; do not replace working main files wholesale.
- Feature `job_reads.scoped_jobs` restricts monitor reads by `MonitorAssignment`: preserve historical authorization for legacy views only if needed but **do not apply it to shared frame review**. New queue endpoints permit all monitors, scoped by event and claims.
- Feature full-density extraction changes storage and throughput relative to existing one-fps import. Preserve original timestamp/density semantics, test on real short video, measure disk/memory/time before making full density the default.
- Run current Python unit/integration tests, frontend typecheck/build, migration check, historical annotator/job tests and a live **non-destructive** Supervisely smoke test. No live event reset, no deletion, no new native jobs without organiser approval.
- Before deploying any schema upgrade, back up PostgreSQL and source media. Never use `docker compose down -v` or `git push --force`.

**Responsibility:** Luís prepares merge resolution, tests and migration chain; Tomas reviews the final patch, preserves frontend/native workflows and approves the merge to `main`. Only after success should both continue development on `main`.

## Phase 1 — Freeze contracts (one small shared checkpoint)

Luís publishes versioned Pydantic/JSON contracts and database migration proposal **before** new UI is connected. Tomas accepts contract names/example payloads before editing the review UI.

- `POST /api/v1/review-queue/claim-next`: returns `review_id`, claim token/expiry, `source_media_id`, `annotation_snapshot_id`, `source_filename`, `team_name`, `annotator_name`, image URL, immutable annotation JSON, geometry/classes, rubric version. Empty queue is a normal no-work result.
- `POST /api/v1/review-queue/{id}/complete`: claim token, per-object verdicts, incorrect reason and corrected-class candidate when applicable, action `next|pause`; saves once and optionally atomically attempts next claim.
- `POST /api/v1/review-queue/{id}/release`: authenticated release for pause/navigation; safe concurrency and lease expiry.
- `GET /api/v1/review-queue/status`: pending, active claims, completed, per-team reviewed/available, next availability, no annotation bytes.
- `GET/PUT /api/v1/scoring/rubric`: monitors read, only organiser updates; explicit immutable version for each decision.
- `GET /api/v1/rankings/teams`: correctly annotated keypoints, correctly annotated boxes, combined primary rank, weighted secondary score, reviewed frames, available frames, errors by reason.
- Native job identity contract covers `source_media_id`, `native_image_id`, `native_job_id`, `job_id`, `assignee_person_id`, `team_id`, `correction_round`, `snapshot_revision` and exact geometry IDs; no media bytes in snapshot download.

## Ownership boundaries — no simultaneous edits to same files

| Owner | Scope | Exclusive implementation files |
| --- | --- | --- |
| **Luís** | Backend data contract; Alembic/migrations; native user sync & identity; outbound publisher and job queue; JSON snapshot integrity; inbound review queue leases/fairness; per-item scoring engine; rankings; native bbox opacity implementation and SDK tests; load benchmarks | `migrations/versions/*`, `src/annotatiathon/models.py`, `native_worker.py`, `native_operations_api.py`, `native_sync_api.py`, `supervisely_client.py`, `labeling_api.py`, `manual_jobs_api.py`, `reviews_api.py`, `review_dashboard_api.py`, `native_export_api.py`, `work_queue.py`, `job_reads.py`, newly created `review_queue_api.py`, `scoring_api.py`, tests for those modules, benchmark scripts |
| **Tomas** | React navigation and role-specific tabs; image/overlay grading UI; queue buttons and ranking presentation; event-day ticket/activity/check-in UI; organiser staff/setup UI; modal system; table scroll/zoom; organiser schedule/auth/access and check-in backend | **All** `web/src/*` after branch integration, `src/annotatiathon/auth.py`, `staff_api.py`, `registration_api.py`, `people_api.py`, `attendance_api.py`, `activities_api.py`, `tickets_api.py`, `settings_api.py`, `events_api.py`, `teams_api.py`, frontend tests and tests for those routes |
| **Tomas integrates** | FastAPI router registration, root navigation, shared dependency wiring, merged live Compose configuration | `src/annotatiathon/app.py`, `web/src/api.ts`, `web/src/main.tsx`, `web/src/styles.css`, `compose.live.yaml`, release notes |
| **Luís proposes; Tomas reviews** | DB columns required for Tomas's event schedule, check-out and scoring config | Migration/model changes solely by Luís. Tomas requests fields in the contract doc; **do not both edit `models.py`**. |

For shared contract changes, post the proposed payload/example in the plan/issue first; owner commits the implementation. Never both open edits in the same file at once. No frontend changes by Luís after Phase 0; no queue/model/worker changes by Tomas. Existing owner assignments may be changed only by mutual agreement.

## Ordered work packages / milestones

| Gate | Tomas | Luís | Acceptance |
| --- | --- | --- | --- |
| **0. Integration** | Review existing main workflows, approve merge | Reconcile branch, migrations and tests | Exactly one Alembic head; main data preserved; CI/build pass |
| **1. Contracts** | Specify desired interaction states, approval of queue/review/ranking examples | Specify snapshot/claim/rubric/rank contracts, migration | Concrete tested request/response fixtures before UI wiring |
| **2. Foundation (parallel)** | Role-segregated event/setup navigation, internal dialogs, tables and check-in/checkout, participant-only counts | Team-independent job/frame identity, deterministic snapshot ingest, additive review queue schema, monitor-agnostic claims | Two monitors cannot claim same frame; organiser-only setup protected |
| **3. Main review workflow (parallel)** | Full-width zoomable overlay and per-annotation verdict controls; Complete & Next/Pause states | Fair team-first random claims; heartbeat/recovery; per-item verdict persistence, reason codes; rankings | Two monitors, three teams, randomized but balanced distinct frames; raw participant JSON retained |
| **4. Event and Supervisely polish (parallel)** | Editable organiser event time window, activity validation; metrics/rubric setup/read-only UI; remove browser prompts; scroll improvements | Native membership refresh, job publication and reliable JSON ingestion; bbox default verification/configuration; annotation timing capability probe; throughput improvements | Join→ID sync→job→submission→JSON→review→rank works with real team |
| **5. Release validation** | Cross-browser/tunnel event rehearsal; feedback on performance and UI; role/permission checks | API/worker migration/recovery, concurrency, snapshot completeness and benchmark | No destructive deployment action; backups and reproducible rollback/recovery strategy |

## Test scenarios that block completion

- Organiser sees setup+event modules; monitor sees all event modules but no setup; backend returns 403 on staff-role edits, media deletion, resets, event closure or credential changes by monitors; both can access legitimate event operations. Luís has organiser+monitor only if his Nightjar account explicitly receives those roles; native Admin alone is insufficient.
- One video per native labeling job per annotator; outgoing publication enqueued once and correct native assignee ID found after join/refresh; one returned JSON per submitted original source frame; no image re-download or source-ID collision.
- Shared queue: three teams with different submission volumes, two simultaneous monitors, repeated claim/release/crash: no double ownership; fairness counters remain within one frame of the lowest eligible team where claims exist; tied eligible teams and frames chosen randomly; claims are not ordered by labeling-job arrival. Incomplete annotations, empty JSON and later correction rounds remain auditable.
- Every keypoint and box has its own decision. Wrong-class reason stores the suggested correct class without mutating original submission. Wrong placement and sabotage are tracked separately. Rankings use actual correct objects, not accepted-frame count; configurable scoring versions do not retroactively rewrite prior decisions.
- With an already assigned team table, participant check-in and check-out work in one click, no native browser prompt; last team member checkout clears table. Monitor roster entries do not appear in attendance denominator.
- Activities cannot start/end outside the configured event interval; timezone display correct at day boundaries.
- Monitor-friendly table scroll, sticky headers, zoom/pan overlay and no document-level double scroll on list-heavy tabs.
- Project-level bbox opacity: **visually inspect a newly published native job with an annotator account**. Confirm 0 fill, visible border and keypoints; if SDK setting is unsupported, report the limitation and retain a documented UI setting rather than claiming it was applied.

## Same-`main` collaboration protocol (mandatory)

1. **No more development on the feature branch after merging**; keep it as a historical reference (do not delete without approval). Never force-push `main`. No new branches. Both use their own local working copies but the same remote `main`.
2. Before editing: `git status --short`; `git switch main`; `git pull --ff-only origin main` (if local commits exist, fetch and rebase safely before push). Confirm file ownership in this plan.
3. Commit **small, domain-contained** increments with passing focused tests and clear messages (`review-api: ...` vs `event-ui: ...`). Push to `main` frequently; if non-fast-forward, `git fetch origin`, inspect, rebase only unpublished local commits, rerun tests and push normally.
4. Announce any required cross-owner file modification before touching it. Only Tomas registers new FastAPI routes in `app.py`; Luís supplies the route module and its contract. Only Luís changes `models.py` and migrations. Dependency version bump or Compose changes require a shared integration slot.
5. Only **one person deploys to the live test stack at a time**. A code push is **not** an instruction to restart the live worker. Before schema/worker changes: backup live DB and media, run `alembic current/heads`, inspect job queue in-flight work. No `down -v`, no event reset, no duplicate Supervisely uploads.
6. Both test each other's integrated work: Tomas tests queue and annotation provenance from UI; Luís tests API semantics for roles, attendance, scoring and dashboard. A phase is complete only when its stated acceptance tests pass, not merely when code is pushed.

## External SDK verification

- Supervisely Project Settings documents **Rectangle opacity**, **Rectangle border size** and **Default opacity** under Image Project > Basic Labeling Toolbox > Visuals: https://github.com/supervisely/docs/blob/master/data-organization/project-dataset/project-settings.md
- SDK `ProjectApi.update_settings(project_id, settings, merge_with_current=True)` exists in published API docs; exact per-project JSON key and whether it overrides annotator preferences still require verification: https://supervisely.readthedocs.io/v6.74.14/sdk/supervisely.api.project_api.ProjectApi.html
