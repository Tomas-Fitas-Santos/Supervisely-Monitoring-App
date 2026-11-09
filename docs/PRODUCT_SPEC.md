# Product specification — Annotatiathon v1.0

## Objective
Support approximately 131 two-person annotation teams, roughly nine monitors, and a long in-person bird annotation event. One native Supervisely team, ID 125980, is used for all members. Participants annotate in Supervisely. The independent event app handles setup, admission, ticket management, scheduling, monitoring, reviews and final exports.

## Staff application
Professional browser login; organiser, monitor, reception, activity, distribution and read-only permissions may be combined. Individual sessions, fine-grained server-side authorization, audit log and responsive screens. Only authenticated staff access the administration and competition workspaces.

## Authentication and Supervisely settings (agreed)
- Staff sign into the **Annotatiathon application**, not Supervisely, using their event staff account. Neither the login page nor normal monitor/reception views ask for a Supervisely API key, user ID, Team ID or Workspace ID.
- A distinct, **organiser-only Settings → Supervisely Connection** page configures the Supervisely server URL, native Team ID and source Workspace ID. The working baseline team is 125980; workspace selection remains editable.
- The settings page displays **Test connection**, verified organiser account, accessible native role, workspace-to-team relationship, last successful synchronisation and a clear error state.
- Native membership changes, dataset upload, job creation, annotation retrieval and other Supervisely operations require authorised server-side Supervisely API credentials. A single organiser-managed credential is entered/rotated in the secure Settings page **only when needed**, never by each monitor. The credential is never included in normal UI payloads, logs, exports or public configuration.
- The backend must encrypt stored service credentials using an application encryption key outside the database; allow replacing/revoking them. Local deployments can alternatively use a secret supplied through the process environment. The UI only shows whether a credential is configured, never its value.
- Workspace and Team IDs are validated by the actual Supervisely API before being marked verified. Other IDs (project, dataset, video, labeling job) are discovered and recorded automatically during operations rather than requested from users during login.
- Configuration can be saved independently of roster, media and competition setup. Missing credentials do not block logistics; Supervisely-dependent actions show what still needs configuration.

## Logistics (agreed)
- Roster CSV import populates PostgreSQL. Edit, add, deactivate and search participants/monitors. Teams have two participants; monitors are pinned first in team views. Sorting includes names and check-in order.
- Fast QR-assisted or name/email/team lookup check-in; staff confirms identity. Duplicate scans do not duplicate attendance. No Supervisely identity capture at check-in.
- Ticket categories start with pizza, drinks and massage, and are organiser-configurable. Awarded/allocated, physically issued and redeemed must be distinct. A participant's history links each action to its source and staff actor.
- Issue/grant to one person, selected people, teams, filtered groups, all checked-in people or everyone. Bulk operations have immutable recipient snapshots, a preview and idempotent batch execution.
- Soft warnings only, never hard blocking. Defaults: warn when a new issuance exceeds 2 pizza tickets in the rolling last 60 minutes or exceeds 3 drink tickets in the same window. All thresholds, time windows, enabled states and categories are configurable. Massage has no default alert.
- Activities have schedule, location, responsible staff, states, participant/team results and rewards. Winners can receive food, drink, massage or custom category tickets automatically, with no duplicate awards on retry. Track ticket grants and physical handout separately.
- Ticket redemption is optional until the physical-ticket scanning workflow is selected. Never report issued tickets as consumed.

## Setup (independent tabs, arbitrary order)
- People & Teams: CSV ingestion, team view and individual view; monitoring team pinned; filters for joined, pending invitation and not invited. Native status is marked confirmed only after verifying Supervisely.
- Invite via native Supervisely website: copy up to 50 deduplicated comma-separated emails of one native role. Sending remains manual until authorised automated invitation is proven.
- Native team administration for permitted staff: view joined members, refresh memberships, inspect native roles, change roles/remove members only after explicit confirmation and API success.
- Dataset: resumable folder/ZIP upload with staged progress; validate complete videos and ordered frames, preserve canonical IDs and source checksums; upload to Supervisely asynchronously.
- Distribution: configurable target frames per team, morphology balancing, optional overlap; complete videos never split. First job may have a smaller soft frame target than later jobs. Preview team workload, overshoot, category balance and unique vs duplicate coverage.
- Job plan: preserve logical prepared jobs in PostgreSQL prior to event start; native visibility and pair collaboration require an integration proof.
- Evaluation: versioned criteria for keypoint, bounding box, frame and temporal consistency; weights and deterministic temporal/stratified frame sampling. Avoid clustered nearly identical frames; retain sampling provenance and distinguish random estimates from targeted reviews.

## Competition
At event launch, distribute the first eligible job to every team. Monitor statuses, throughput, frame counts, team totals, review backlog and scores. Submission triggers completeness validation. Incomplete work returns to the same team; complete work releases next queued job while sampled human review proceeds. Organiser can manually intervene, assign additional work, pause or reallocate. Independent duplicated media must never share writable annotation objects between teams.

## Closure
Stop new releases, reconcile active/incomplete/submitted native jobs, capture annotation versions and media-to-source mapping, preserve independent duplicate labels, build full provenance export plus a separately curated version, validate manifests and provide resumable local download.

## Synchronisation
Updates every few seconds only on the active browser tab, preferably pushed from SQL transactions. Inactive tabs reload on entry, plus manual sync. Supervisely reconciliation is separate from browser rendering, with last-success timestamps and visible failures.

## Release criteria
The event cannot be called production-ready until live multi-account native job dispatch, correctness, failure recovery, performance, full export, and database restore have been proven.
