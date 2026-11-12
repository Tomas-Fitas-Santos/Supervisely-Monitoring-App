# Nightjar 300-person load-test protocol (isolated simulation + small live rehearsal)

**Goal:** determine a defensible publisher count on the actual event machine:
300 annotators, 150 two-person teams, 12 monitor browsers, 30 frames per
job, approximately 90 seconds per frame. At a sustained pace this means
about 6.7 completed jobs/minute, 200 submitted frames/minute, or 4,800 jobs
and 144,000 frames over 12 hours. A 300-job submission burst is a separate
worst-case scenario, **not** a realistic steady-state throughput assumption.

## Safety boundaries

- The **300-person benchmark never contacts live Supervisely**. It uses
  `compose.benchmark.yaml`, a private internal-only Docker network, a
  fake `supervisely-sim` API endpoint, and separately named PostgreSQL
  and media volumes. All 300 users and jobs exist only in this isolated test.
- The **real Supervisely measurement** uses only the existing live Nightjar
  worker with its optional timing file and **3–5 existing test accounts**
  against an explicitly selected disposable Supervisely test project.
  Pre-mirror a small set of images once; do not bulk-upload files or create
  hundreds of native jobs. Never run extra live workers to measure capacity.
- No secrets, full URLs, query strings, annotation JSON, emails, headers or
  request bodies are written to timing logs. Stage records include internal
  job IDs, so keep raw traces under ignored `.benchmark-data/` and never
  publish them. Aggregate simulator profiles contain only duration statistics.
- Preserve `.env.live`, database volumes, media directories and native
  assignments. Never use `docker compose down -v` with the live project.
  The sweep cleans up *only its explicitly named isolated benchmark volumes*.

## A. Calibrate against the real Supervisely server (small test)

1. Pull latest `main`; deploy the upgraded worker once after backing up
   your current live Nightjar database using `scripts/deploy-live.ps1`.
2. Add this optional setting to the **existing** `.env.live`, exactly once:
   ```dotenv
   NIGHTJAR_WORKER_TIMING_FILE=/data/metrics/supervisely-timing.jsonl
   ```
3. Start the same live worker with timing enabled. Do **not** scale this
   live worker to 2, 3, 4, or 300 processes:
   ```powershell
   docker compose --env-file .env.live -f compose.live.yaml --profile native up -d --build --force-recreate worker
   ```
4. In a **disposable test project**, pre-mirror a few frames, have 3–5
   testers annotate their existing jobs, submit individually, then submit
   together within a few seconds. Include one deliberately incomplete job
   to measure correction publishing. This is the **only** step making
   real native writes, for the few jobs deliberately prepared.
5. Record the participant's Submit click and replacement visibility with
   a screen recording or stopwatch. Worker instrumentation cannot observe
   a user's browser Submit click or when the Supervisely UI renders a
   newly created job.
6. Copy private timing data from the worker:
   ```powershell
   New-Item -Type Directory -Force .benchmark-data | Out-Null
   $id = docker compose --env-file .env.live -f compose.live.yaml --profile native ps -q worker
   docker cp "${id}:/data/metrics/supervisely-timing.jsonl" ".benchmark-data/real-worker.jsonl"
   python scripts/analyze_worker_timing.py .benchmark-data/real-worker.jsonl --report .benchmark-data/real-timing-report.json --profile scripts/benchmark_profiles/measured.json
   ```
7. Inspect per-category p50/p95/p99 and stage lags. The captured timings
   include status polls, annotation reads, native job creation and lookups.
   Missing stages or categories mean the small rehearsal did not exercise
   them; **do not claim the resulting profile is statistically representative**
   until enough samples are collected. Keep the raw file private.

### Worker timeline semantics

- `submission_detected` is when the real poll first confirms a submitted
  job. The extra Submit-click-to-detection delay must be derived from the
  tester's recording.
- `frames_validated` is after downloading and structurally validating the
  job's annotation JSON. `correction_queued` marks a partial return.
- `correction_publish_started` / `correction_publish_confirmed` measure
  correction publication, and `publish_started` / `publish_confirmed`
  measure the normal next-job publisher.
- The confirmed publication timestamp is **not** proof that an annotator's
  browser has rendered the job. Record that final visible delay separately.

## B. Isolated 300-person worker-count sweep on Windows

Open PowerShell at the repository root, ensure Docker Desktop has the
**Linux engine running**, and use the measured aggregate profile created
above. These commands do not access Supervisely:

```powershell
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario burst
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario burst -ProfileScale 2.5 -TimeoutRate 0.01 -FailureRate 0.01
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario staggered
```

Only if total RAM, CPU and database lock pressure leave sufficient headroom,
optionally run `-Workers 4`. The Windows machine previously reported
about 15.4 GiB Docker Desktop memory, so four 2 GiB workers plus API,
database, simulator and load driver can approach the limit. Do not assume
that more workers always improve throughput.

Results are stored under `.benchmark-data/sweep-300-*/workers-N/`:
- `resources.jsonl`: per-container CPU percentage (100% = one CPU core),
  working-set RAM, and collection timestamps.
- `worker-timing.jsonl`: per-request durations and worker milestones.
- `requests.json`: monitor/browser HTTP responses, sizes and latencies.
- `result.json`: submitted jobs, native operations, API stats and simulator calls.
- `summary.json`: CPU/RAM peaks, endpoint p95, errors, publication-cohort
  timing (first, 290th and last confirmed replacement **if reached**).
- `comparison.json`: a compact worker-by-worker summary.

The slowdown profile applies delays only inside the fake Supervisely API.
Real Nightjar worker/DB processing, lock waits, queueing and HTTP API
latency remain actual, not sleeps injected into Nightjar itself. The
`-TimeoutRate` fault emits delayed **synthetic HTTP 504 responses**;
these exercise error handling, but do not perfectly model a socket-level
timeout. Worker number is evaluated independently in a clean test project.

## C. Selecting the worker sweet spot

Compare 1 vs 2 vs 3 workers under the **same** burst, duration,
profile and fault settings. Prefer the smallest worker count that:

1. Keeps Nightjar API and monitor interaction p95 preferably under
   1 second during bursts, with no sustained HTTP 5xx.
2. Clears the publication backlog at least as quickly as the
   **6.7 jobs/minute** normal arrival rate, with enough burst capacity.
   Compare first/290th/last publication; missing 290th at the end
   means the worker count has **not** caught up in that run.
3. Leaves CPU, PostgreSQL connection/lock capacity and RAM headroom
   (target at least 20–25% free memory), including media caches.
4. Does not create duplicate native jobs, lost accepted frames, or
   double-counted correction rounds.

Do not declare a sweet spot from machine specs or synthetic SDK latency
alone: complete the small live rehearsal and inspect each measured run.
A 300-person attendance total does **not** mean 300 Nightjar logins;
Nightjar directly serves monitors/admins and worker-to-Supervisely calls.
