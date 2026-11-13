# Nightjar worker architecture — 300 automated annotators

**Status:** phased implementation. Benchmark multi-role workers are enabled in
`compose.benchmark.yaml`. `compose.live.yaml` retains one `--role all`
job-processing worker until the 300-person safety gates pass, but runs
separate, CPU-bounded `indexer` and `mirror-worker` services for local media
preparation and Supervisely uploads.

## Non-negotiable requirements

- Exactly **300 simulated annotators**, 150 teams, 12 simulated monitor
  sessions and consecutive 30-frame submissions. No manual annotator activity.
- Normal workload ~6.7 jobs/min, 200 frames/min if a frame takes 90s;
  benchmark also uses 300 near-simultaneous submissions.
- Supervisely real-world calibration has a strictly bounded read-only probe
  and an **optional** 1–3 controlled create/delete probe in an isolated
  benchmark project. Never produce 300 real jobs or upload thousands of files.
- Structurally complete annotation JSON from a partial job enters the monitor
  review queue **immediately**. The incomplete images alone return as a
  smaller correction job to the same participant. Already accepted frames
  remain immutable and are reviewed once, even across correction rounds;
  all rounds share one original Nightjar logical job.
- Reviewer UI and original local frames remain fast and independent of
  remote Supervisely availability. Scoring formulas are unchanged.
- Keep PostgreSQL, existing Docker deployment and production environment.
  No Redis/RabbitMQ, new hosted services, or demo mode in production.

## Worker roles (isolated benchmark)

| Role | Replicas | Work | Safety |
| --- | ---: | --- | --- |
| `status` | 1 | Poll active native jobs and atomically enqueue durable validation | No remote writes |
| `validation` | 1, 2, 4 (test) | Download annotation JSON, check completeness, ingest accepted frames, queue correction or next block | Per-round operation, fenced lease, heartbeat, 5 bounded retries |
| `publish` | 1, 2, 3, 4 (test) | Issue and reconcile uniquely named next native job | Row-level SKIP LOCKED on *one* NativeOperation; uncertain writes require attention, never blind replay |
| `correction` | 1 | Reassign or create subset correction jobs | Matching native title/person/dataset; no duplicated Nightjar jobs |
| `recovery` | 1 | Reclaim only expired, idempotent validation leases; handle event resets | Does not blindly repeat uncertain remote writes |
| `bulk` | 1 | Dataset mirroring and export | Separate from high-priority submission flow |
| `api` + `web` | 1 each initially | Ticket/check-in/review/leaderboard requests | Do not wait for Supervisely |

Every worker role has an independent PostgreSQL session and SDK client.
Publishing and validation can be tuned independently with
`scripts/run_worker_sweep.ps1 -Workers 1,2,3 -Validators 2`;
repeat with `-Validators 1` or `-Validators 4`.

**Implementation details:** `validate_submission` tasks reuse one
`native_operations` row per job. Their payload records the correction
round and native job ID. Submission status and validation task are saved
in one DB transaction. A validation task is claimed under
`FOR UPDATE SKIP LOCKED` and assigned a renewable, fenced lease.
After a crash, recovery may retry only this *read-only* validation task,
not an uncertain native job creation. Immutable
`round-N/manifest.json` files and per-frame review uniqueness prevent
duplicate scoring; missing/invalid snapshots cause a bounded retry.
After validation, the same worker attempts to queue the next eligible
30-frame block immediately instead of waiting for a second whole-status
scan. If allocation fails, the status scheduler can retry.

## Phases and acceptance gates

1. **Baseline measurements (implemented):** per-Supervisely-endpoint
   request timing, detection/validation/queue/publish milestones, CPU/RAM,
   p50/p95/p99 API latency, resource and error logs. Report which numbers
   are measured real calls versus synthetic simulated responses.
2. **Task safety (implemented first pass):** one-row publish claims,
   fenced per-round validation tasks, heartbeats, bounded retries,
   recovery of expired read leases, and snapshot identity checks.
   Validate crash-after-SDK/after-file-write races against PostgreSQL.
3. **Independent roles (benchmark enabled):** one status scheduler,
   independently configurable validators/publishers, correction, recovery
   and bulk roles. The live Compose remains unchanged until tested.
4. **Remove avoidable work (partially implemented):** active-only status
   queries, cheap validation enqueue instead of five snapshots per poll,
   immediate next-block allocation. **Still to profile/tune:** bounded
   parallel Supervisely status reads with a shared request budget,
   metadata caching, DB lock analysis, selective active polling.
5. **Tune concurrency (requires actual run):** compare same 300-person
   burst at 1/2/3 publishers and 1/2/4 validators, normal and slow/429
   simulated conditions. Never infer the sweet spot from CPU alone.
6. **Rehearse release (not yet complete):** soak with historical workload,
   test worker killing mid-validation and just after remote job creation,
   reset/pause races, disk-full/DB disconnect, rollback/migrations and
   queued operations recovery.

## Completion criteria

- No duplicate native jobs after uncertain remote responses; any
  ambiguous create stays in *attention* pending exact reconciliation.
- Each accepted source frame has at most one structurally complete entry
  in review across submission rounds, including corrections.
- No user holds two active WorkPackets; returning incomplete frames does
  not free that annotator's original work slot.
- 300 simultaneous submissions drain with bounded queue delay and a
  final replacement latency within the event's accepted target. A
  **provisional 60-second target** was used for analysis only; it is NOT
  yet verified or agreed as a production SLA.
- Monitor browser actions stay responsive, preferably API p95 <1s,
  with no sustained 5xx errors. CPU and RAM stay within available
  hardware headroom; PostgreSQL lock waits and memory peaks are reported.
- No supervised bulk load test writes to the real Supervisely service.

## Capacity sizing: two automated scenarios

Use the isolated benchmark to choose worker counts for **each stage**, not
just the total API latency. The test modes never contact the real Supervisely
API, and the live environment is not scaled.

* `-Phase initial`: activate and enqueue **300 new jobs together**. Time
  from durable queue entry to the first, 150th, 290th, and 300th confirmed
  native job. No monitor approval is involved. This isolates publisher
  capacity and includes confirmation overhead. If the 300th job is still
  missing, the report states the outstanding count; extend `-Seconds`.
* `-Phase turnaround`: start with 300 jobs submitted in a burst, execute
  production completeness validation on all 30 frames/job, automatically
  return incomplete jobs to the **same participant**, and enqueue new
  30-frame blocks for fully completed jobs. Default `-IncompleteRate 0.20`
  makes 60/300 original jobs each lack one head landmark. Such frames
  must never reach the review pool. No synthetic monitor accepts jobs.
  Output counts validated review-ready frames, correction publications,
  follow-up publications and any remaining work.

The synthetic fixture intentionally reuses image content across many
independent assignments for throughput measurement. It is **not** the
competition's unique-frame allocation/fairness test. Publishing and API
latency use simulated Supervisely replies; `-AssumedPublishMs` is a
model assumption until calibrated against bounded live tests.

Compare dimensions independently rather than testing every combination:

```powershell
# 300-job release: choose 1, then 2, then 3 publishing workers.
.\scripts\run_worker_sweep.ps1 -Phase initial -Workers 1,2,3 -Validators 2 -Seconds 900 -Scenario burst

# Full turnaround, including 20% incomplete submissions.
.\scripts\run_worker_sweep.ps1 -Phase turnaround -Workers 1,2,3 -Validators 2 -Seconds 480 -Scenario burst -IncompleteRate 0.20

# Isolate validation capacity while holding other roles fixed.
.\scripts\run_worker_sweep.ps1 -Phase turnaround -Workers 2 -Validators 1 -Seconds 480
.\scripts\run_worker_sweep.ps1 -Phase turnaround -Workers 2 -Validators 4 -Seconds 480

# Isolate correction and status scheduler capacity separately.
.\scripts\run_worker_sweep.ps1 -Phase turnaround -Workers 2 -Validators 2 -Correctors 2 -Seconds 480
.\scripts\run_worker_sweep.ps1 -Phase turnaround -Workers 2 -Validators 2 -StatusWorkers 2 -Seconds 480
```

Review `summary.json` for `publication_cohort`,
`submission_detection`, and `worker_timing.stage_lags`. These
distinguish detection, durable queue wait, validation execution,
incomplete-job correction queue/publishing, and complete-to-next-job
turnaround. Each isolated run records actual running worker replica counts.
To choose production roles require a completed 300-job cohort, negligible
pending work, zero unexpected errors/duplicates, and acceptable p95/p99
participant wait time under normal and slow/failure latency profiles.
Do not infer a production staffing number from an unfinished cohort.

## Running the automated benchmark

From repository root on Windows with Docker Desktop Linux engine running:

```powershell
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 2 -Seconds 480 -Scenario burst
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 2 -Seconds 480 -Scenario staggered
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 4 -Seconds 480 -Scenario burst -ProfileScale 2.5 -FailureRate 0.01
```

The runner computes a content fingerprint of the API/native/frontend Docker
build inputs and records it in `.benchmark-data/worker-sweep-build.sha256`.
It builds the three shared benchmark images **once** on the first run, when
build inputs change, or when an image is missing. Subsequent sweeps and
publisher/validator variants reuse those images without calling Docker build.
Changes to `scripts/benchmark_full.py` or the load scripts need no rebuild:
they are mounted read-only into the benchmark containers.

To explicitly request an image rebuild, append `-Rebuild` to the command.
Each variant still creates a unique Compose project and clean, independent
PostgreSQL/media volumes; teardown removes only those test containers/volumes,
not the cached images or live Nightjar stack.

**These commands are test-only.** They use a private Docker network with a
synthetic Supervisely endpoint and a separate database. The independent
`nightjar-live` project, users, real native jobs and real dataset are not
modified by the sweep. See `docs/LOAD_TEST_300.md` for the optional
bounded read calibration and create/delete latency probe.

Do not deploy role-scaled live workers until the race, crash, migration,
load and rollback gates above are verified.
