# Architecture and data ownership (local-first)

## Components
- **Frontend:** React + TypeScript, professional responsive interface with staff login and role-based workspace navigation.
- **Backend:** FastAPI with versioned domain routes, Pydantic contracts, access checks, SQLAlchemy and Alembic migrations.
- **Database:** local PostgreSQL is authoritative for attendance, registrations, competition teams, staff identities and roles, tickets, activities, scoring, job plans, provenance and audit trails.
- **Background processing:** durable Postgres-backed worker/outbox for upload, reconciliation, native job release, completeness checks, sampling, exports and bulk awards. Separate worker processes can share the local machine.
- **Supervisely adapter:** isolated API layer for Team 125980; Supervisely remains authoritative for native membership, uploaded media, labeling job states and actual annotations.

## Non-negotiable boundaries
- Ordinary check-in, ticket issuance and activities never call Supervisely.
- Roster email does not equal a verified Supervisely login; invited-but-not-accepted differs from joined.
- Before launch, prepare logical jobs in PostgreSQL. Do not assume newly created native Supervisely jobs are hidden.
- Complete videos only; soft frame targets for teams and for jobs (smaller configurable first job). Duplicate video assignments have distinct native copies and preserve canonical source identity.
- On submission, structurally validate coverage; release next job only when valid. Quality sampling runs independently and can issue corrections.
- Keep annotation snapshots and scoring provenance; final export preserves partial work and independent duplicate labels.
- Unique request identifiers, transactional state updates, optimistic locking for user edits, and audited privileged actions.

## Key domains (migration groups)
1. Identity: staff accounts, sessions, roles and audit.
2. People: roster imports, participants, monitors, competition teams, membership reconciliation, table/check-in.
3. Tickets: categories, time-window advisory policies, allocations, issuances, redemptions, reversals, distribution batches.
4. Activities: schedule, registrations, winners, awards, reward-to-ticket ledger.
5. Media: uploads, video inventory, original frame IDs, Supervisely dataset/video IDs and replicas.
6. Competition: plans, logical jobs, native job links, progression/assignment events, monitor ownership and manual overrides.
7. Quality: rubric versions, deterministic sampled frames with temporal spacing, frame/box/keypoint review, scores.
8. Closure: event freeze, in-flight state captures, annotation revisions, export manifests and artifacts.

## Real-time strategy
The browser loads only the selected tab and subscribes to its topic via SSE (or incremental polling if required). The backend notifies after committed SQL transactions. Supervisely metadata comes from a reconciliation worker; the UI shows freshness and failures. Inactive tabs refresh when opened.

## Security and operational gates
- No public access before staff authentication and authorization are complete.
- Native Supervisely credentials reside only server-side, scoped to least privilege.
- Strict ownership and audience checks for person-level records.
- Migrations are reviewed, reversible where possible, and preserve audit history.
- Before a hosted rollout: TLS, backup/restore drill, CSP/CSRF/session security audit, worker recovery, concurrency load test and data retention policy.

## External technical validations still required
1. Verify two annotators can safely contribute to a single logical team answer without overwriting each other.
2. Verify native Supervisely video job creation, visibility, submission, correction, and state reconciliation with authorised real accounts.
3. Verify completeness semantics including intentionally unannotated frames.
4. Verify API permissions for native team role changes and removal. Invitation copying and manual native invitation remain the baseline.
