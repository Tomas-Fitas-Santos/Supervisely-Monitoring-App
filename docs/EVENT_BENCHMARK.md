# Event capacity experiment

Measured on 10 November 2026 using `scripts/benchmark_event.py`.

The event workload was subsequently clarified as **30 images per job**. This earlier experiment used ten images per job. See `FULL_EVENT_BENCHMARK.md` for the production-stack experiment and its confirmed workload.

## Workload and environment

- Windows host with 12 physical cores, 20 logical processors, and 15.8 GiB RAM.
- Python 3.11.9 in an isolated environment. The production project declares Python 3.12 or newer, so these measurements need confirmation on the production runtime.
- An isolated SQLite WAL database, created from the real SQLAlchemy models. Docker and PostgreSQL were unavailable on the test host.
- Ten authenticated monitor sessions using real FastAPI handlers over localhost HTTP.
- Each monitor requests the 260-job list, assignment overview, an assigned job's review information, and an original JPEG every five seconds. This is a deliberately synchronized read workload of eight HTTP requests per second. The JPEG is approximately 2.25 MB. Ten source images are reused across jobs, with independent native-image mappings.
- Each scenario lasts approximately 120 seconds and makes 960 HTTP requests.
- 260 single-assignee jobs, 130 teams, 260 participants, and ten monitor assignments.
- Ten images per job. Cohorts of 26 jobs submit at the start and at 60 seconds, representing 260 image completions per minute. Further cohorts become eligible at subsequent minute boundaries.
- The real `sync_all` function runs in a background thread in the API process, followed by the production 12-second sleep. The simulated SDK implements job status and annotation JSON downloads with fixed per-call delays. Each image has 30 synthetic rectangle annotations.
- CPU and memory include the API process tree and worker thread. They exclude the load generator. One CPU core means one CPU-second per wall-clock second. Resident memory is sampled once per second and can miss brief peaks.

## Results

| Simulated SDK delay per call | Average CPU cores | Peak resident memory | HTTP p95 | HTTP errors | Completed job snapshots at stop |
| --- | --- | --- | --- | --- | --- |
| 0 ms | 0.58 | 116.5 MiB | 1.84 s | 0 / 960 | 49 |
| 50 ms | 0.61 | 116.6 MiB | 1.92 s | 0 / 960 | 20 |
| 200 ms | 0.65 | 116.6 MiB | 2.80 s | 0 / 960 | 5 |

Peak private memory was approximately 96 MiB in each scenario. Each scenario transferred approximately 580 MB to the simulated monitors.

At least 52 jobs submitted by the end of the second cohort. The snapshot counts demonstrate incomplete processing of that cohort in all scenarios. Jobs arriving at the 120-second boundary have essentially no processing time and should be excluded when judging the backlog.

With zero injected latency, completed synchronization passes took approximately 0.7–1.9 seconds, followed by the 12-second sleep. At 50 ms per SDK call, passes took 17.0–17.9 seconds. At 200 ms, the first complete pass took 64.5 seconds. Its next pass was still running when the measurement ended.

The Windows filesystem raised transient `PermissionError` exceptions during ten snapshot attempts in the zero-latency run and one attempt in the 50 ms run. The existing worker logged failures and retried on later passes. The underlying cause was not established. These failures affect throughput and prevent treating the zero-latency run as a clean filesystem baseline.

## Findings

The observed Python service memory and average CPU use are modest for this short synthetic event. These measurements do not establish a requirement for 16 GB RAM. They also do not establish peak CPU needs or include production database and SDK memory.

The worker cannot sustain the assumed ten-image-job submission rate at either 50 ms or 200 ms per external call. `sync_all` checks active jobs sequentially and captures at most five completed jobs per pass. The sleep happens after all work. At 50 ms, five snapshots roughly every 29–30 seconds is around ten jobs per minute, below the assumed 26 submissions per minute. A larger CPU allocation alone will not remove external-call waits or this limit.

Job size changes the result. At 50 frames per job, 260 image completions per minute correspond to 5.2 job submissions per minute. Annotation downloads still grow with the number of frames, so the ten-frame measurements cannot directly certify that configuration.

The job-list handler loads all event bindings and performs repeated individual person lookups. This pattern deserves profiling and batching before using a remote PostgreSQL database, especially as historical jobs accumulate.

## Deployment implications

- Retain 4 vCPUs and 8 GB RAM with managed PostgreSQL as a provisional deployment budget. The extra capacity covers production dependencies, concurrency spikes, and operating-system caches. Confirm it on the chosen Linux VM.
- Address synchronization throughput before relying on the app for this event. Separate status polling, annotation capture, and publishing work, then introduce bounded concurrency with appropriate API limits and safe task ownership.
- Audit publishing separately. `run_worker` calls `process_once` once per iteration, so next-job releases share the synchronization cycle. This experiment did not execute publishing or test uploads.
- Batch the job-list person lookups and limit event-binding reads to relevant jobs, then repeat the workload against PostgreSQL.
- Repeat using the real expected frames per job, annotation complexity, image dimensions, and deployment filesystem.

## Limits

This experiment covers read requests and snapshot synchronization over two minutes. It excludes monitor review writes, password hashing during simultaneous login, real SDK transport overhead, real Supervisely latency/rate limits, TLS, PostgreSQL, Docker, publishing, dataset import, exports, browser rendering, a complete original dataset, and twelve hours of historical growth. API and worker share one interpreter here, whereas the Docker deployment uses separate processes. The test was not CPU- or RAM-limited to a proposed VM size.

The CPU and RAM values therefore measure this diagnostic workload and cannot certify production capacity or event availability. Worker snapshot throughput failure under the specified conditions is directly observed.

## Reproduction

Install the app and `psutil` and `Pillow` in an isolated Python 3.12+ environment, then run from the repository root.

```powershell
python scripts/benchmark_event.py --seconds 120 --latencies 0 0.05 0.2
```

The script starts only localhost services and uses synthetic SDK responses. It creates fresh synthetic databases and images under ignored `.benchmark-data/`. It does not use real Supervisely credentials or mutate an existing application database. Summary results are written to `.benchmark-data/latest.json`, with requests and worker logs retained in individual scenario directories.
