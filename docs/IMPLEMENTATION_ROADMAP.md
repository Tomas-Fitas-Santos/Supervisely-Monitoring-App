# Local-first implementation roadmap

**Version:** 1.0 | **Development branch:** `main` ONLY | **Deployment:** deferred until local completion.

Phases are sequential for delivery, while setup screens in the final app must allow independent configuration.

| Phase | Work | Required acceptance gate |
|---|---|---|
| 0. Specification and repository | Freeze product, architecture, rules; create clean `main` foundation | No old branch merge or personal-data artifacts; CI passes |
| 1. Local core | Local PostgreSQL, migrations, FastAPI, React UI, professional staff login, session/permission model, SSE, background worker | Two real local staff accounts have isolated access and see committed changes across browsers; restart preserves data |
| 2. People and attendance | CSV ingestion/mapping, versioned imports, team and people views, staff management, check-in, table assignment | Reimport is idempotent; duplicate scans safe; concurrent changes correct |
| 3. Tickets and activities | Soft limits, individual/bulk grants, issued/redeemed ledger, activity schedule, team/individual prizes, audit | Batch retries never duplicate awards; warning never blocks; history reconciles |
| 4. Supervisely membership | Native team 125980 read/sync, role changes/removal with permissions, 50-email copy, manual invitation reconciliation | Verified join/role changes reflected; non-verifiable pending invites stay unconfirmed |
| 5. Media and scheduling | Chunked ZIP/folder upload, validation, video source mapping, fair whole-video allocation, soft frame limits, logical prepared jobs | Balanced preview, repeatable plan, no split video, no native job sent during preview |
| 6. Native competition operations | Verify pair semantics, create/reconcile native video jobs, event launch, progress, completeness, automatic/manual release and correction | Real two-account test, safe retry/timeout recovery and no duplicate native jobs |
| 7. Human quality evaluation | Versioned weighted rubrics, stratified temporal sampling, frame/box/keypoint grading, quality dashboard | Reproducible sample, independent targeted reviews, audited scores |
| 8. Closure/export and hardening | Freeze, partial-work retrieval, duplicate provenance, full/curated archives, load tests and backup restoration | Full recovery and export with 131-team-scale synthetic load and verified live integration |

## Local delivery rules
- Each phase ends with functional tests and a tested UI, not just classes or endpoint stubs.
- Use `main` exclusively. Never open/create a feature branch without express permission.
- No sample people, DEMO mode, fake native submissions, production data in Git or unintended real Supervisely mutations.
- Existing branch implementations are read-only study material. Do not fast-forward or merge their history into public `main`; it contains historical roster artifacts.
- Local PostgreSQL stays authoritative during development; preserve migrations and never wipe data to fix logic bugs.
- Document blockers and operational questions rather than inventing SDK capabilities.
- Hosted deployment is planned **after** the full local app passes all acceptance gates; avoid hosting cost decisions meanwhile.

## First engineering delivery
This initial commit creates the backend package, local PostgreSQL Compose service, documented configuration, health/ready endpoints and a test workflow. It does **not** claim the professional login or event functions are implemented.
