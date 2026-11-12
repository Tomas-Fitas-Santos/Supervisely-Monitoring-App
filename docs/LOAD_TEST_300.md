# Fully automated 300-annotator performance test

The entire 300-person benchmark is **automatic**. No real annotator needs
to sign in, annotate or click Submit. No bulk uploads and no hundreds of
jobs on the real Supervisely account.

## What is tested

- 300 synthetic annotators in 150 two-person teams
- 12 concurrent simulated monitors using real Nightjar web/API endpoints
- 30 source frames per job; modeled 90 seconds of human annotation per frame
- Baseline: approximately 6.7 job submissions per minute
- Burst: 300 jobs submitted within a short interval
- Real Nightjar PostgreSQL contention, worker processing, job allocation,
  per-frame structural validation, correction handling and review queue
- 1, 2, 3 (optionally 4) native worker containers
- HTTP latency, p50/p95/p99, publication backlog and throughput, CPU and RAM
- First, 290th, and last replacement-jobs' confirmation times, where reached

The simulated Supervisely server runs ONLY on the private Docker benchmark
network. It can create and delete thousands of synthetic jobs without
sending a single request to real Supervisely. The benchmark uses its own
Docker project, PostgreSQL database and media volumes. It never deletes
`nightjar-live` or production data.

## Optional: small automatic REAL Supervisely calibration

These are separate from the 300-annotator simulation, and require **no
human annotators**. Their only purpose is to measure realistic network/API
request durations, which are then replayed in the isolated 300-annotator
test.

### Step 1: bounded read-only calibration

Once the latest application is deployed and its existing native worker
running, execute:

```powershell
docker compose --env-file .env.live -f compose.live.yaml --profile native exec -T worker python -m annotatiathon.supervisely_probe --samples 3 --min-interval 1.5
```

This automatically performs at most nine sequential *read-only* requests
(three account/team/workspace lookups and up to three existing job and three
annotation requests), no faster than one every 1.5 seconds.
On access/rate errors it stops instead of retrying. No new files or jobs
are created. It saves only aggregate response-time statistics under
`/data/metrics/read-only-profile.json`.

### Step 2: optional REAL create/delete probe

**Yes: it is possible to create and delete a real labeling job without
uploading any files or needing an annotator to submit it.**

Supervisely SDK calls: `api.labeling_job.create()`, `get_info_by_id()`,
`api.labeling_job.remove()`. The SDK's removal is **permanent**.

This command is deliberately **opt-in** and will refuse to write outside
an existing Supervisely project whose name begins with
**`Nightjar Benchmark`**. Create or designate a small disposable project
and dataset first, with at least one already available image. Identify one
**verified test member**, not an active event participant. Nothing in the
probe creates, edits or deletes images or datasets.

Run once, supplying those three actual numeric IDs:

```powershell
docker compose --env-file .env.live -f compose.live.yaml --profile native exec -T worker python -m annotatiathon.supervisely_write_probe --allow-create-delete --dataset-id DATASET_ID --image-id IMAGE_ID --test-user-id USER_ID --repeats 1
```

Replace the three `*_ID` placeholders with the test IDs before running.
For a slightly more representative sample, use `--repeats 3`
(maximum permitted; at least two seconds between jobs).

For each iteration, the code:

1. Checks that the existing test dataset belongs to the currently configured
   Supervisely workspace and a project named `Nightjar Benchmark...`.
2. Checks that the test image belongs to that dataset.
3. Creates **one** temporary job using that single existing image.
4. Measures creation, confirms the assigned test user and dataset.
5. Removes the **exact new job**, never an existing event assignment.
6. Confirms the job no longer exists before allowing another iteration.

A failed/ambiguous request triggers identity-based cleanup. If cleanup
cannot be confirmed, the probe **stops** and requires checking the test
project manually. Do not rerun it blindly on an unresolved failure.

The write probe merges its measured `publish` timing with the earlier
read-only `status`, `annotation` and `lookup` timing groups in
`/data/metrics/benchmark-calibrated.json`.

Copy the anonymous aggregate profile into the local benchmark:

```powershell
New-Item -ItemType Directory -Force scripts/benchmark_profiles | Out-Null
$workerId = docker compose --env-file .env.live -f compose.live.yaml --profile native ps -q worker
docker cp "${workerId}:/data/metrics/benchmark-calibrated.json" scripts/benchmark_profiles/measured.json
```

The calibrated profile contains only count/p50/p95/p99/mean durations per
category, **no passwords, tokens, people, URLs, image IDs or annotation data**.
If you elect to skip the optional real probes, the 300-person benchmark
remains fully functional using explicitly labeled synthetic latency values.

## Step 3: automated 300-annotator worker sweep

Run in PowerShell from the local repository root with Docker Desktop:

```powershell
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario burst
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario staggered
.\scripts\run_worker_sweep.ps1 -Workers 1,2,3 -Seconds 480 -Scenario burst -ProfileScale 2.5 -TimeoutRate 0.01 -FailureRate 0.01
```

If an anonymous measured profile is available, the fake Supervisely service
uses it for its request delays. Otherwise it uses synthetic fallback timing.
`-AssumedPublishMs 1000` controls **modeled** job-write latency when no
write measurement is present. Results cannot claim real write capacity in
this case. Run all worker counts with identical timing and fault profiles.

Start with 1, 2 and 3 workers. Only consider 4 if memory and database
connection headroom permit it on the target machine (previously Docker
reported ~15.4 GiB). More workers are not necessarily better.

### Inspect the results

Each sweep writes to `.benchmark-data/sweep-300-*/`, with a
`comparison.json` and per-worker `summary.json` files. These report
measured application response times, container CPU/RAM, operation errors,
simulated Supervisely throughput, and confirmed replacement progress.
A worker count is suitable only if the system handles both steady-state
arrivals (~6.7 jobs/minute) and bursts without unbounded queues or
degraded monitor responsiveness.

Never run `docker compose down -v` on the live project. The sweep has
its own isolated Compose project and cleans up only that benchmark.
