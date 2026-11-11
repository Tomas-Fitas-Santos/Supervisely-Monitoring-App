# Production-stack event benchmark

This experiment uses the confirmed event workload of 260 annotators, 30 images per job, and one minute per image. A job lasts approximately 30 minutes. Continuous annotation produces approximately 8.67 job submissions per minute, 6,240 jobs over 12 hours, and 187,200 image annotation completions.

The earlier SQLite experiment in `EVENT_BENCHMARK.md` used an assumed ten images per job. Its submission rate and backlog results describe that earlier assumption.

## Installed environment

Docker Engine and Docker Compose were installed in the existing Ubuntu 24.04 WSL2 distribution. The benchmark uses Python 3.12, PostgreSQL 17, the compiled production React frontend, Nginx, the real application API, and the real Supervisely SDK worker in separate containers. PostgreSQL starts from a fresh database using all production Alembic migrations.

The initial lifecycle and sensitivity runs used the native image for Python containers. That image contains FastAPI 0.136.3 because of SDK dependencies. Follow-up monitor/upload and historical-data checks used the deployment's actual API base image with FastAPI 0.162.4, alongside the native SDK worker. Both images use Python 3.12.16, SQLAlchemy 2.1.8, and psycopg 3.3.8. The worker uses Supervisely SDK 6.74.54. The checked-in Compose file now selects the separate production API image.

`compose.benchmark.yaml` uses its own database and media volumes and an internal Docker network. Synthetic credentials and a locally generated TLS certificate are used throughout. The simulator hostname is allowlisted and its certificate is trusted by the SDK. The API and worker entrypoints remain the application's normal entrypoints.

The four measured services have CPU quotas totaling four cores and memory limits totaling 6.75 GiB, leaving a nominal margin within an 8 GiB machine. The external-service simulator and monitor load generator run separately and are excluded from those service totals. The host supplies additional CPU and memory for those test drivers. This arrangement represents constrained application services on a larger WSL host.

## Workload

- Ten separate monitor accounts log in concurrently through HTTPS using real password verification and secure session cookies.
- Each monitor polls job and assignment APIs every five seconds and browses original JPEGs.
- For a submitted job with a completed snapshot, its assigned monitor fetches every original image and annotation JSON, saves a score for every frame, and accepts the job through the real API.
- The real worker detects native submissions, captures annotation snapshots, queues follow-up jobs, and publishes them using real SDK HTTP methods.
- Original JPEGs are approximately 2.25 MB at 1600 by 1200 pixels. Thirty synthetic source files are reused across jobs, which have independent native-image identities.
- Annotation JSON contains three synthetic birds per image, each with one rectangle and four visible landmarks. These fifteen objects follow the app's annotation classes. Occlusion, masks, and complex polygons are outside this fixture.
- Simulated Supervisely responses normally delay each request by a deterministic random 25–75 ms. HTTPS, SDK parsing, connection handling, and PostgreSQL transactions execute normally. The simulator implements the SDK's actual nested job metadata and annotation response formats.
- The steady run starts with 260 in-progress jobs at staggered ages and another 260 queued follow-up jobs. Jobs submit at an average of 8.67 per minute. Publication delays can prevent the application from keeping every annotator supplied with another job.
- The lifecycle run includes a chunked ZIP import, ten minutes of monitor activity, event archival, current-annotation ZIP export, and concurrent job-list reads after generating the database volume of 6,240 jobs and their native-image mappings.
- A separate short burst run makes all 260 initial jobs submit together. This covers the concentration of submissions that can follow a synchronized event start.
- A separate cold-upload case creates a remote dataset and sends all thirty original JPEGs through the real SDK's hash checking, multipart binary upload, image registration, and job creation methods. The steady/lifecycle and burst cases use pre-existing simulated remote media. The upload case gives source files unique bytes to exercise all thirty binary transfers.

## Measurements

Docker CPU and working-memory samples are collected roughly every five seconds. Linux cgroup `memory.peak` captures brief memory peaks, including concurrent logins, and includes memory categories that differ from Docker's cache-adjusted working-memory display. Component peak values can occur at different times. CPU throttling counters and container exit/OOM state are retained.

The load generator retains HTTP status, duration, transferred bytes, monitor acceptances, simulator call counts, snapshot counts, and native operation states. The full lifecycle also validates that the downloaded export is a readable ZIP with valid entry checksums.

## Verified results on 10 November 2026

| Case | Verified observations |
| --- | --- |
| Ten-minute steady workload, 25–75 ms simulated external response delay | 88 submissions were due, 83 job snapshots were ready, monitors accepted 83 jobs and saved 2,490 frame scores. Only 15 follow-up jobs were published, with 62 publishing operations pending. |
| 260 simultaneous submissions, approximately two minutes | 20 snapshots ready, 19 jobs accepted, two follow-up jobs published. All 5,742 monitor HTTP requests succeeded. This is the corrected actor run. |
| 100–300 ms simulated external response delay, approximately two minutes | 18 submissions due, four snapshots ready, three jobs accepted, zero follow-up jobs published at measurement stop. All 1,050 monitor HTTP requests succeeded. |
| Exact production API plus cold-upload publishing, approximately two minutes | All 1,851 HTTP requests succeeded, twelve jobs were accepted, and a follow-up job was published after transferring 30 JPEGs totaling 67,565,570 bytes through the real SDK. |
| Historical-data reads with the exact production API | All ten monitors explicitly selected the target event. Each received 2,000 job records, including 1,832 historical jobs, from a database containing 6,240 jobs. Each concurrent job-list request took approximately 35.2–36.1 seconds. |
| Handler SQL profile | Returning 2,000 jobs executed 9,951 SQL statements inside the handler, including 9,947 queries against people. The isolated handler invocation took 3.43 seconds, while ten competing HTTP requests took roughly 36 seconds each. |
| Chunked ZIP import | Imported 67,419,932 bytes containing thirty images. Finalization took approximately 0.39 seconds. |
| Closeout export | Exported 8,250 annotation copies in approximately 496 seconds. The downloaded ZIP contained 8,253 entries and passed entry checksum validation. This export covers the test's published images. |

The lifecycle suite made 11,410 successful HTTP requests. Twenty requests in its original historical-login/read subphase were later invalidated for capacity assessment because they selected a different event. The corrected historical figures above come from separate reruns with event selection and returned-row assertions. The ten-minute review/publishing measurements and import/export validation remain applicable.

The first burst actors waited on the first unready job rather than scanning for ready snapshots. They were corrected, and the burst test was repeated. Use the verified burst case above for review and publishing conclusions.

### Resource consumption

| Component | Maximum observed cgroup memory across the suite |
| --- | --- |
| API | 771 MiB |
| SDK worker | 228 MiB |
| PostgreSQL | 402 MiB |
| Nginx | 25 MiB |

These component peaks occurred at different times and include categories beyond Docker's cache-adjusted working-memory display. The largest sampled combined working-memory value was approximately 904 MiB. The API's brief login peaks are substantially higher than its usual roughly 120–140 MiB working memory. Argon2 uses 64 MiB per concurrent password verification.

The active monitor workload generally used around 1.5–1.6 CPU cores across the four measured services. The largest sampled combined CPU use across the reviewed runs was approximately 1.8 cores. The API accounted for most CPU time. CPU averages over the entire lifecycle are lower because the long export waits on external requests. Samples can miss brief CPU peaks, and some service CPU throttling occurred within the configured quotas.

All reviewed runs completed without container OOM kills. The exact production API follow-up reproduced both successful image publishing and the roughly 36-second historical-query slowdown.

### Interpretation

Four vCPUs and 8 GiB RAM remain a provisional deployment budget for the complete stack, including local PostgreSQL. The observed workload leaves memory headroom within that budget. Production disk capacity, a larger image working set, operating-system overhead, and future concurrency changes still require allowance.

The current worker's publishing cadence and the job-list query pattern prevent relying on machine size alone for the event. Publication needs scheduling independent of the complete reconciliation/snapshot pass. The job-list handler needs batched person lookups and bounded binding reads. Repeating this benchmark after those changes will show their effect.

The closeout export issues individual annotation downloads. Its elapsed time is sensitive to external round-trip latency. The measured export is smaller than a full event's theoretical 187,200 annotation copies, so budget additional closeout time until export throughput is improved or measured at full volume.

### Retained artifacts

All paths below are relative to the repository's ignored `.benchmark-data/` directory.

- `full-20261110T191907Z` contains the steady lifecycle, import, export, and historical fixture creation.
- `full-20261110T194141Z` contains the corrected burst run.
- `full-20261110T193309Z` contains the slower external-response case.
- `full-20261110T194641Z` contains the exact production API and binary-upload verification.
- `full-20261110T194654Z` contains the exact production API historical-read verification.
- `full-20261110T194132Z/query-profile.json` contains the handler SQL count, with the initial image versions alongside it.

Each run retains request records, simulator statistics, worker logs, resource samples, cgroup memory peaks, and container state. `scripts/analyze_full_benchmark.py` produces a summary from these files.

## Reliability and coverage

These measurements exercise real server code, database locking, authentication, TLS, SDK serialization, and successful review/publishing/export workflows. They can identify server bottlenecks under the specified workload and show measured resource consumption.

The simulator supplies annotation content and completion timing. Actual Supervisely capacity, authenticated API response times, service throttling, browser rendering, annotation quality, and venue Wi-Fi require measurements in those environments. HTTP actors reproduce requests and decisions without rendering the React interface. The current review UI displays an original image and a JSON inspector, so visual annotation overlays remain separate frontend work.

The source images are a small reused fixture. This favors filesystem cache hits and does not reproduce a complete original-image collection or its disk capacity requirements. The historical-data phase tests accumulated database volume, including native mappings, without creating a complete twelve-hour collection of annotation snapshot files. A ten-minute workload plus historical records does not measure twelve hours of uninterrupted operation, memory leaks, or long-term recovery.

The HTTP actors score frames programmatically and have no human inspection delay. This measures server processing of review operations. Actual monitor review speed and judgment require a human rehearsal.

Successful workflow coverage uses good frame scores and accepted jobs. Correction rounds, outages, and partial-failure recovery require additional cases. Synthetic external latency and available host resources must be considered when choosing a deployment machine.

## Reproduction

From Ubuntu with Docker available, build the production runtime and prepare the frontend/database images.

```bash
docker build --target native -f deploy/Dockerfile.api -t nightjar-benchmark-runtime .
docker build --target base -f deploy/Dockerfile.api -t nightjar-benchmark-api .
sudo bash scripts/run_full_benchmark.sh setup
sudo bash scripts/run_full_benchmark.sh run 600 0.05 0 nightjar-benchmark-30
```

Use a fresh project name for a fresh fixture. A project retains its synthetic database and media volumes after the runner stops the containers. Reusing an already completed project also reuses its modified event state. The runner stops the containers at completion and retains per-run results under ignored `.benchmark-data/full-*/` directories.

For the burst case, use another fresh project.

```bash
sudo env BENCHMARK_BURST=1 bash scripts/run_full_benchmark.sh run 120 0.05 0 nightjar-benchmark-burst
python scripts/analyze_full_benchmark.py .benchmark-data/full-TIMESTAMP
```

For cold-image uploads, set `BENCHMARK_UPLOAD=1` and use another fresh project. To repeat historical reads after a lifecycle run, use the existing populated project.

```bash
sudo env BENCHMARK_UPLOAD=1 bash scripts/run_full_benchmark.sh run 120 0.05 0 nightjar-benchmark-upload
sudo bash scripts/run_full_benchmark.sh history 0 0.05 0 nightjar-benchmark-30
sudo bash scripts/profile_full_benchmark.sh nightjar-benchmark-30
```

`scripts/benchmark_pull_images.sh` supplies an optional temporary IPv4 registry-resolution workaround for this host's intermittent unreachable IPv6 registry addresses. It removes its temporary hosts entries after setup.
