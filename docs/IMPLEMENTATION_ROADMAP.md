# Nightjar implementation status and acceptance gates

**Updated:** 2026-11-10 · **Branch:** `main` only · **Local-first:** no DEMO mode.

| Phase | Local implementation | Still required before deployment |
|---|---|---|
| 0 — Architecture | Clean codebase, contracts, production-oriented `main`, no sensitive roster in Git | Operational deployment plan |
| 1 — Server | FastAPI, Argon2 staff authentication, verified allowlist-based monitor self-registration, CSRF, PostgreSQL migrations, multi-role staff UI, React UI, authenticated polling | Live SMTP and multiple-browser load/recovery tests |
| 2 — People & event logistics | CSV import, manual name/email check-in and shared team table, independent monitor responsibilities, manual and balanced assignment, multi-event archive/reset, two-monitor trial teams | Live full-scale team roster rehearsal |
| 3 — Rewards | Tickets/categories, bulk distribution, issue/redemption/limits, activities, prize awards, reversal audits | Venue-side workflows and staff usability rehearsal |
| 4 — Supervisely membership | Organiser-only team/workspace and encrypted integration token in Settings, personal account verification, auto import/sync for joined monitors and participants, explicit monitor account approval, manual invitation-email handoff | Native invitation from existing login is not currently exposed as an in-app workflow; real account access and role test |
| 5 — Dataset | Resumable ZIP/folder imports; species/morphology inventory; source-hash provenance; whole-video jobs; frame-balanced, redundant distribution | Full dataset capacity, storage and image-corruption exercise |
| 6 — Competition and submission | Explicit first-job release, durable SDK queue, one pair workload at a time, separate native job ID per annotator, per-user status sync; monitor must accept before next release; recoverable SDK correction/restart with history | Live two-account shared-editing semantics and native reviewer behavior |
| 7 — Scoring and monitoring | Job/media inspection, sampled frame and keypoint scoring, temporal consistency flag, audit and monitor assignments | Final scoring rule calibration with organisers; timing and quality rehearsal |
| 8 — Closeout | Event archive, CSV reports, native annotation JSON ZIP full/curated manifests with source provenance | Real partial export and disaster-recovery restore exercise |

## Binding acceptance criteria

1. **One active pair-level job:** never publish a later job until the previous
   job is accepted locally by the responsible monitor (both native accounts
   must have submitted). A partial submission, paused plan, returned review
   or uncertain remote write must not advance the team.
2. **Two-monitor test pairing:** organiser can form exactly two existing
   monitors into a competition pair, without changing either staff/native
   role; can disband before job planning or after all jobs are accepted with no unfinished native operation; old job history remains.
3. **Corrections:** a rejected pair stays on the same logical job. SDK
   restarts keep old native job IDs for audit and require resubmission.
   The retry action reconciles existing native records after interrupted writes.
4. **No silent remote effects:** uploading and planning are local; remote
   publishing requires explicit launch and a separately running native worker.
   No credentials are stored in the browser or repository.
5. **Multi-event provenance:** no force deletion of source people, tickets,
   native jobs or prior accepted annotations. Archived events remain
   available and current settings are shared where appropriate.
6. **Real Supervisely collaboration is not established by API unit tests.**
   SDK `labeling_job.create(user_ids=[a,b])` yields two native job records.
   Confirm that the two volunteers can cooperate on the intended single
   annotation answer with their actual permissions before event day.

## Local verification

From the repository root on Windows with Docker Desktop running and the
existing `.env` retained:

```powershell
git switch main
git pull --ff-only origin main
docker compose -f compose.local.yaml --env-file .env up -d db
.\.venv-nightjar\Scripts\Activate.ps1
alembic upgrade head
alembic current
python -m pytest -q
python -m ruff check src tests migrations
```

Run the API with `uvicorn annotatiathon.app:app --reload --host
127.0.0.1 --port 8000`, run the frontend from `web` using
`npm.cmd install` and `npm.cmd run dev`, and open
`http://127.0.0.1:5173`.

To use optional live SDK publishing install
`python -m pip install -e ".[native]"` and then start
`python -m annotatiathon.native_worker --loop --poll-seconds 12`
in a separate terminal. Configure, verify and reconcile your native
Supervisely team/workspace beforehand. The organiser's encrypted integration token is read from PostgreSQL;
`NIGHTJAR_ENCRYPTION_KEY` remains a private deployment secret.
Do not start native publishing merely to test local UI or CRUD operations.

The latest CI run for the pair queue includes PostgreSQL migrations, Ruff,
backend tests and the React build. Consult GitHub Actions for the authoritative
current pass/fail status before any live event rehearsal.
