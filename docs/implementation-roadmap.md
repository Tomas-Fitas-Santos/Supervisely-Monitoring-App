# Investigation findings and next steps

The attached operational plan is the workflow source. Its team and dataset quantities are estimates, not configuration constants. This repository intentionally has no fixed event capacity.

## Confirmed integration surfaces

| Need | Supervisely capability | Application decision |
|---|---|---|
| Concurrent monitors | Native multi-user SDK mode, minimum SDK 6.73.454 and instance 6.15.2 | One shared app in the monitoring team; per-browser interaction state |
| User permissions | `session_user_api`, `user.get_my_info`, team membership | Verify current request credential and context agree; restrict assigned participant teams |
| Job preparation | `labeling_job.create`, explicit entity IDs, reviewer assignment | Create only released tasks; split image batches explicitly between the pair |
| Progress | Job info and `get_stats` | Use confirmed images and submitted job states; no synthetic frame completion |
| Contributions | Filtered team activity | Recent labeling-action counts, clearly separate from quality and throughput |
| Image review | `set_entity_review_status` | Internal durable decisions and a separate native status publication action |
| Annotation inspection | Native image/video labeling tools | Open current or historical jobs with the monitor's own permissions |

Sources:

- [Native multi-user sessions](https://developer.supervisely.com/app-development/advanced/multi_user_session)
- [Pinned SDK labeling-job reference](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.labeling_job_api.LabelingJobApi.html)
- [Labeling job workflow and roles](https://docs.supervisely.com/labeling/jobs)
- [Collaboration and cross-team sharing](https://docs.supervisely.com/collaboration)
- [App configuration](https://developer.supervisely.com/app-development/basics/app-json-config/config-json)
- [Private app installation](https://developer.supervisely.com/app-development/basics/add-private-app)

## Refinements to the plan

1. Distinguish the monitoring team (app audience), participant teams (annotation boundaries), and the organiser/service identity (provisioning and read-only global polling). Supervisely team permissions and the app's assignment permissions must both hold.
2. Keep original source IDs and team-local destination IDs separately. Independent teams must never edit the same destination annotations. Validate coverage against the entire source inventory, including unassigned sources.
3. Prepare future work in the app database, rather than exposing all jobs with a `pending` status. Release creates access only after the monitor's decision.
4. Make replication, workload units and batch size inputs. Preserve the one-complete-final-video rule and report incompatible actual inventories instead of silently dropping videos or splitting them.
5. Count submitted/confirmed work separately from structural annotation validity and monitor approval. One frame may contain several birds, and a visually empty frame is not necessarily unfinished. Counts of figures are not completion metrics.
6. Preserve every review transition in audit history, including later corrections on approved batches. The current decision can change while its history remains intact.
7. Treat uncertain remote creates as an integration state that requires reconciliation. Supervisely and PostgreSQL cannot form one atomic transaction; a database lock alone is insufficient to guarantee remote exactly-once creation.

## Current implementation limits

- A participant pair has two disjoint image jobs. Its full video has one lead annotator's job. Simultaneous joint video editing or controlled handover must be verified with Supervisely before this is considered event-ready.
- Image decisions can be published, but text correction notes are internal. Video frame flags are not whole-video acceptance. Automatic notifications, correction-job creation and in-flight annotation revision checks are not implemented.
- Batch approval is the monitor's explicit recorded decision. It does not currently finalise the native reviewer job. Native review completion and rejected-job tracking need a tested lifecycle integration.
- Manual native image status publication is a separate external operation from local review persistence. It is not an atomic dual write; an operator must reconcile conflicting or failed publication, and native status audit/automatic synchronisation is follow-up work.
- Event setup creates or registers participant teams using existing accounts, assigns monitors, uploads raw image/video data, previews allocation and copies independent team-local entities. New user accounts are not created. Interrupted writes require manual resource inspection; automatic resumable provisioning is not implemented. Storage deduplication, quota accounting, licence/team limits and privileges must be confirmed on the actual instance.
- Schema validation checks assignment structure and frame bounds. It does not inspect every bounding box, bird identity, anatomical node or visibility tag. The final metadata schema needs to be agreed and then validated on annotations.
- Export covers monitoring records only. Raw annotations, source checksums, job closure, collection retry checkpoints and review bundles are next-phase work.
- The current schema holds one event per database. Multiple simultaneous monitors are supported by design; unrelated events should use separate databases until explicit event scoping is implemented.

## Verification performed for this initial version

- 79 local Python tests passed using disposable SQLite databases. Coverage includes encrypted local connections, organiser/server pinning, separate event groups, registered people, unassigned pairs, balanced monitor plans, legacy group adoption, access isolation, concurrent workflow changes, releases and interrupted provisioning.
- The pinned SDK imported and the UI rendered its HTML template and served its JavaScript. A local ASGI request to the dashboard refresh endpoint returned HTTP 200.
- JavaScript syntax checks passed. The dashboard component compiled and rendered in Vue 2 with a simulated DOM; two separate component instances retained independent filters and team selection.
- No live Supervisely credentials were supplied. GitHub Actions exercises the suite on disposable PostgreSQL and Windows. Native hosted-session behavior still needs the live pilot.
- Browser screenshot verification could not run because Chromium download failed in this environment. Full browser visual QA remains a pilot check.
- An earlier local TestClient lifespan shutdown exposed a pinned SDK/dependency incompatibility: its internal `async_asgi_testclient` rejects Starlette's `http.response.debug` message while caching the root template. Root rendering and refresh requests succeeded, but clean shutdown/offline caching must be checked on the actual agent image. Resolve the SDK/dependency combination before event deployment rather than patching installed dependencies ad hoc.

## Ordered implementation steps

### 1. Real pilot with Event setup

Use [the real pilot walkthrough](real-pilot.md), a configurable roster and your sample assets. Test two distinct monitors and two browser tabs for the same monitor. Verify isolation, revoked membership, assigned-team access, open-in-tool links, image confirmation counts, video submission, native entity list response shape, concurrent releases and ambiguous-create recovery. Measure API latency, polling-cycle duration and rate limits; increase the stale threshold only from measured results.

### 2. Preparation and distribution

The setup UI now supports exact registered user logins, team registration, monitor assignment, browser media upload, distribution previews, independent entity copies and durable operation/resource checkpoints. Next add CSV roster import, queued setup operations, crash-safe automatic reconciliation and native reviewer handover after monitor reassignment. Confirm whether copying can share media storage while keeping annotations independent. Provision the annotation schema and event guide consistently. Extend source mapping reconciliation and allocation quality checks. Balanced pair-to-monitor assignment and monitor reassignment history are implemented.

### 3. Complete review and correction workflow

Attach frame-aware issues/messages to the source job, persist notification delivery outcomes and make corrections visible to participant pairs. Track rejected-image correction jobs returned by Supervisely and bring their contributions/progress into the original batch. Test reopening past batches, native review finalisation and partner video handover. Add annotation revision tracking to avoid treating an old review as approval of newly edited labels.

### 4. Annotation checks and final collection

Validate per-bird boxes, crown/eyes/beak, visibility states and video frame associations using the agreed metadata. Keep structural errors distinct from scientific ambiguity. Collect independent image and video annotation payloads to durable storage with source identity, participant team, annotation timestamps, metadata version, monitor history and checksums. Resume partial collection safely; provide unresolved-case review packages.

### 5. Rehearsal and capacity test

Use the actual planned monitor count, team count, asset inventory and estimated API traffic. Test shared polling under concurrent browsers, database restart, app restart, remote outages, lease expiry, delayed job creation and storage limits. Run usability sessions with people unfamiliar with Supervisely. Use their measured annotation rate to set two-hour image batches and then perform a small end-to-end rehearsal before scaling up.

## Real pilot setup update

- Admin authorization is checked on every setup request using the current user's monitoring-team membership. Manager monitors see only their assigned teams.
- Live local testing derives the user ID from the configured API token instead of trusting synthetic LOCAL_USER_ID. Routes read incoming request state directly, avoiding a shared form race between tabs for the same user.
- Distribution previews check the complete selected inventory and metadata again on apply, retain stable source IDs, create distinct destination entities and publish locked batches atomically.
- Setup operations serialize across organisers; unknown outcomes retain confirmed resource IDs and block new setup/release actions until inspection. Staged upload chunks are bound to their uploader and offset.
- The dashboard uses the SDK/Element primary blue #20a0ff with its neutral and status colors. Vue templates render in a simulated DOM; a browser screenshot could not be produced because the Chromium download was truncated.
- Live platform provisioning, job workflows and native simultaneous-user sessions remain checks for the real pilot. Automated remote tests use mocked SDK responses and actual SDK signature/metadata validation.

## In-app event groups update

- Fresh local starts initialize their database without event environment settings. The connection screen authenticates the organiser once and saves an encrypted credential; reconnecting is pinned to the event owner/server. Local background polling uses it. Hosted mode uses the current native user credential and never persists that token.
- Event setup creates/adopts Monitors and Annotators native groups, remembers their IDs and source workspace, accepts multiple registered logins and enforces separate logical rosters. Native administrative ownership is retained separately. Existing event people are adopted during upgrade.
- Participant pairs use two selected unpaired annotators and may start unassigned. Manual assignment and balanced allocation previews grant participant-team roles and save monitor ownership. Unassigned pairs cannot receive dataset distribution.
- Fresh hosted onboarding requires a launch-team Admin. The Monitors group uses that launch team. Group membership and assigned-pair access are checked on each request; non-Admin monitors cannot read setup rosters.
- Vue simulated DOM checks covered group entry, deferred pairing, assignment previews, uploads, independent browser state and keeping connection credentials outside widget state. Fresh Uvicorn startup, root/script serving and rejected invalid connection requests were checked over local HTTP.
