# Implementation and capability status

## Implemented

- Native hosted multi-user sessions with credential-owner checks, assigned-team enforcement, current Admin authorization, organiser oversight/adjudication and per-browser selections.
- CSV column mapping for one pair per row or two participant rows per team, exact email matching where visible, audited manual identity verification for hidden email, duplicate checks and idempotent registration imports.
- Native pair provisioning using existing accounts, manual/balanced monitor assignment, released-job reviewer handover, pre-release unassignment and operation checkpoints.
- Recursive source discovery and folder uploads, path-preserving source identity, explicit original-to-copy mappings, independent team-local entities and streaming upload checksums.
- Seeded effort balancing, configurable replication, optional common challenge, whole-video assignment, multiple videos per pair, alternating member ownership and explicit native video handover.
- Current/submitted/upcoming batch views, per-member job progress, confirmed native entity counts, paginated image/required-frame observations and stale-data warnings. Frame presence is distinct from completion/quality.
- Immutable captured submission payloads/metadata/digests, historical previews, rule freezing, whole/per-criterion/weighted scoring, configurable penalties, pending/NA handling, audited revisions, provisional standings and ties.
- Native correction tasks with precise issue reports, submission/approval release gates and guarded native acceptance of fully judged unchanged captures.
- Polling-based observed event closure, continued judging of captures after closure and reproducible JSON exports including annotation payloads and audit.
- A persistent, resettable organiser simulator with one-click presentation preparation; the supplied real-email pair CSV and two credited CC0 bird photographs in nested folders. A separate real-account pilot offers the same bundled inputs through actual authenticated actions. Local frontend assets are bundled; hosted sessions retain the native SDK shell.
- Production-only startup guards, database health probe, container recipe and a native release source packager. Deployment requires a validated instance version and your own published container image. GitHub Actions is manual-only; local validation is independent of its quota.

## Validation boundaries

The unit/integration suite uses real pinned-SDK geometry and method signatures, disposable databases, and simulated/mocked remote responses. The Chromium walkthrough runs the actual local server, exercises the three tabs and checks no external browser requests. Optional manual CI supports Windows, PostgreSQL and browser checks; the app has no dependency on GitHub Actions availability.

No live Supervisely credentials were available in this development environment. The [real pilot guide](real-pilot.md) is the acceptance gate for native invitation semantics, permissions, correction/acceptance state transitions, cross-team isolation and simultaneous authenticated platform sessions. Neither the fixture size nor native multi-user support establishes event-scale capacity.

Email-based lookup is constrained by the account's native visibility. The application can use visible email information or audited organiser verification. The supplied real emails alone do not grant API access or reveal private native IDs/logins.

Native job allocation is entity-based. Whole videos are supported; independent native submissions for frame ranges of the same video are not assumed. Splitting oversized videos would require a deliberate clip-generation workflow with original frame mappings and scientific boundary rules.

Captures are observed states, not guaranteed exact submission/deadline-time platform snapshots. Native project versioning may help on eligible instances but is not required by or automatically invoked in this implementation. Closure is a journaled sequence of stop/capture operations, not a distributed atomic transaction.

Judgments are human assessments. Structural checks identify missing classes/tags, duplicates and presence; they do not establish anatomical correctness, missed entire birds or agreement with an unavailable gold reference. Every point-bearing unit needs the agreed judging method. Unsubmitted/unreleased work is not silently classified as missing.

Unknown remote writes are never blindly replayed. Automatic reconciliation of arbitrary interrupted setup operations is still future work; the current recovery path is durable checkpoints plus explicit inspection. Expensive setup/capture work runs outside the async request loop but is not a separate resumable distributed job queue. Size the deployment and rehearse actual inventories before the event.

## Next work after the live pilot

Use the pilot results to address instance-specific API behavior and measured bottlenecks. Potential extensions include reference-based pose comparison with validated tolerances, richer calibration/adjudication tools, background provisioning queues, automated crash reconciliation, clip preparation when necessary, direct read-only Google Sheets registration synchronization and scientific collection formats beyond the complete JSON record export. These are extensions rather than assumed Supervisely capabilities.
