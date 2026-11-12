# Nightjar worker architecture — 300 automated annotators

**Status:** phased implementation. Benchmark multi-role workers are enabled in
`compose.benchmark.yaml`. `compose.live.yaml` deliberately retains the
single `--role all` native worker until the 300-person safety gates pass.

## Non-negotiable requirements

- Exactly **300 simulated annotators**, 150 teams, 12 simulated monitor
  sessions and consecutive 30-frame submissions. No manual annotator activity.
- Normal workload ~6.7 jobs/min, 200 frames/min if a frame takes 90s;
  benchmark also uses 300 near-simultaneous submissions.
- Supervisely real-world calibration has a strictly bounded read-only probe
  and an **optional** 1–3 controlled create/delete probe in an isolated
  benchmark project. Never produce 300 real jobs or upload thousands of files.
- Accepted frames from partial jobs enter review once; incomplete frames
  return as a smaller native job to the same user; all correction rounds
  share one original Nightjar competition job.
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

## Running the automated benchmark

From repository root on Windows with Docker Desktop Linux engine running:

```powershell
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 2 -Seconds 480 -Scenario burst
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 2 -Seconds 480 -Scenario staggered
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Validators 4 -Seconds 480 -Scenario burst -ProfileScale 2.5 -FailureRate 0.01
```

**These commands are test-only.** They use a private Docker network with a
synthetic Supervisely endpoint and a separate database. The independent
`nightjar-live` project, users, real native jobs and real dataset are not
modified by the sweep. See `docs/LOAD_TEST_300.md` for the optional
bounded read calibration and create/delete latency probe.

Do not deploy role-scaled live workers until the race, crash, migration,
load and rollback gates above are verified.
