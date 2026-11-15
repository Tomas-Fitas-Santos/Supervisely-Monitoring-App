# Event health and staged release

The organiser-only **Job Progress → Event health** tab is read-only. It refreshes
every 20 seconds while visible, preserves the last result on request failure,
and never contacts Supervisely, retries operations or changes assignments.
It supplements the existing recovery workflow; it is not an automated failover
system or an external alert delivery service.

## Observations

- Queues: ready, scheduled-backoff, running and attention counts per operation
  type. Ready wait starts at the later of creation and next eligible attempt.
  This avoids using the original creation time of a reused validation task.
  Running duration starts at the latest claim. Expired leases are shown separately.
- Yellow queue warnings: at least 20 ready operations, oldest ready wait at least
  two minutes, an expired lease, or a run exceeding five minutes (30 minutes for
  indexing/mirroring/append/preparation/export/reset). Red: ten-minute ready wait
  or an attention/blocked/failed operation. Thresholds are inspection prompts,
  not proof that a task is stuck.
- Completion rate and average run duration use operations whose latest successful
  finish is within the preceding 15 minutes. These are operations, not frames.
  Reused task rows retain only their most recent completion. Drain estimate is
  ready count divided by that observed rate, assumes no new arrivals and does
  not include running tasks. Backlog change compares ready + running counts
  between successful browser refreshes; it is not a persisted time series.
- Workers: atomic start/finish/error receipts in the shared media directory,
  one per process and role. Missing or older-than-90-second observations warn.
  A long SDK call keeps its original start visible. Idle polling is observable;
  it does not establish remote service health. Errors writing telemetry cannot
  prevent jobs from progressing. Default expected fleet matches live Compose;
  optional `NIGHTJAR_EXPECTED_WORKERS` JSON can override role counts.
- Sync: the oldest sync among active event job bindings, rather than only the
  latest successful sync. A three-minute delay warns. No active bindings is idle.
- Media: up to ten indexed source-image paths per ready dataset, resolved using
  the app's normal source resolver. This is a bounded availability sample, not
  a full filesystem/identity check. Free space describes the media filesystem,
  not Docker's separate PostgreSQL volume or every physical host disk.
- Backup: the existing backup utility now publishes a small non-secret receipt
  after success/failure. Missing receipts mean unknown, not that all existing
  backups disappeared. More than 15 minutes old or a failed attempt warns.
  Receipts do not prove off-machine protection, current archive integrity or
  restored media. Continuous backups are not enabled by this change.

Worker/storage receipts are host-wide; queue, sync and dataset queries are scoped
to the selected event. No schema migration, re-index or dataset replacement is
required for this feature. Deploy API and every worker together to start receiving
telemetry. Pre-upgrade workers will appear as not reporting.

## Prepare without deploying

```powershell
.\scripts\deploy-live.ps1 -StartWorker -PrepareOnly
.\scripts\prepare-tunnel.ps1
```

Preparation validates Compose, retains current application images with rollback
tags and records their IDs under `backups/deployments`, then builds replacement
images. It does not stop/start services, migrate the database, change event data
or start a tunnel. Image preparation consumes local CPU/disk space.

Only after an explicit deployment decision, run the existing command without
`-PrepareOnly`. It builds before downtime, then pauses the services, makes the
database backup and upgrades the complete fleet using the original volumes and
configuration. Keep old image tags until the event is over. If image-only rollback
is needed, stop writers, map each recorded rollback image back to its service image
name and recreate the same services with `--no-build`; do not restore an older
database merely to undo application code. Coordinate rollback of all writer roles.
Never use `down -v` or Clean event as a recovery action.

## API/frontend release while the site stays online

For changes verified to work with the existing database and running workers:

```powershell
.\scripts\backup-live.ps1
.\scripts\deploy-live-hot.ps1
```

The hot-release script refuses changed migration files. It builds while the
existing site serves, starts a separate private API container with the same
configuration and volumes, waits for readiness, then switches the existing
Nginx upstream with a graceful reload. New frontend assets are copied first;
the HTML entrypoint is replaced atomically. Previous hashed assets remain
available to already-open tabs. It does not stop the web container, database,
workers, indexer or tunnel, and does not execute migrations.

Migration-file equality alone does not prove application compatibility. Check
the backend changes and worker compatibility before using this path. Worker
code changes and incompatible schema changes need their own coordinated rollout.
Use `-SkipBuild` only immediately after building the intended API/web images.

Release receipts and previous Nginx/HTML files live under
`backups/deployments/hot-<release>`. The previous API container stays running
for in-flight requests and rollback. If Docker has pruned an old image's layers,
the script saves the previous application files and retains the old container;
it does not attempt to recreate that container from missing layers.

On failure, the script restores the previous upstream and HTML entrypoint.
For a later rollback, copy `previous-nginx.conf` from that release directory to
the serving web container's `/etc/nginx/conf.d/default.conf`, run `nginx -t` and
`nginx -s reload`, and restore `previous-index.html` by staging then renaming it
to `/usr/share/nginx/html/index.html`. Retain the previous API until requests
have drained and the new release is accepted. A later full Compose deployment
recreates the regular API/web services and restores the baked-in `api:8000`
upstream; old hot-release API containers can then be retired after draining.

Release `20261113-210630` deployed the NABirds image-classification correction
and smaller review keypoints using this method. The serving web container initially
routed API traffic to `nightjar-live-api-20261113-210630`; the normal
`nightjar-live-api-1` remains available for rollback. The database and worker
fleet were not restarted. The new frontend asset is `index-B7LYxQgB.js`.

Release `20261113-235358` added monitor rankings with the same live switch.
The current private upstream is `nightjar-live-api-20261113-235358`; the frontend
asset is `index-qW9Zzuzw.js`. Public monitoring recorded 123 successful health
checks and zero failures during the release. No database migration, backfill,
worker restart, web restart or tunnel restart was needed.

### Monitor rankings

Available in **Monitoring & Review → Monitor rankings** and **Results & Rankings**.
The read-only `/api/v1/rankings/monitors` endpoint allows organisers and monitors
and scopes records to the selected event. Default scope is all event reviews;
`period=today` starts at midnight in Europe/Lisbon, including daylight saving.

Ranks use distinct source images per reviewer. Equal image counts share a rank.
The table also shows video-frame and still-image counts, total completed review
records, average review score out of 5, and the latest review timestamp. Repeat reviews increase the completed
review count but not the distinct-image count. Open, released and expired claims
do not count. Completed shared-review records remain credited after later
corrections; historical legacy saved-review rows and inactive reviewer accounts
remain visible. Team prize eligibility and scores are independent.

Historical records are queried directly, with no mutation or backfill. At final
verification the Official Event returned 687 completed reviews from 8 accounts;
all video/still-image breakdowns matched their image totals. Ten focused tests
passed, including event isolation, permissions, shared ranks, repeat reviews,
legacy history and Lisbon midnight. Frontend build and local browser checks passed.

Release `20261114-014528` added the average review score column, displayed with
two decimal places. It averages the same 0–5 anatomical score used in team
results across completed scored reviews in the selected period, including repeat
reviews and genuine zero scores. Historical shared reviews without stored
`frame_points` recover their score from immutable per-object verdicts and annotation
snapshots at read time. Legacy reviews with an incompatible rubric, or records
without recoverable scores, stay credited in contribution counts but are excluded
from the average; a monitor without scored reviews displays “—”. Ranking order
continues to use distinct reviewed images.

Fifteen focused ranking, review and scoring tests passed. Browser checks covered
decimal rounding, zero and unavailable scores. A read-only repeatable-read
production check matched canonical scores for all 1,220 completed event reviews
before release, including both all-event and Lisbon-today scopes. The public
frontend matches the tested build (`index-BMacatcR.js`), the API is healthy, and
135 public health checks recorded zero failures through the release. The serving
web container, workers and database were not restarted; no migration or historical
data rewrite was performed. Current private upstream:
`nightjar-live-api-20261114-014528`.

## Persistent Cloudflare tunnel (not activated by preparation)

1. In the user's Cloudflare account, create a remotely managed named tunnel and
   choose a hostname under their Cloudflare-managed domain. Its origin must be
   **`http://web:80`**: the tunnel container shares the application Compose network.
2. Save only the tunnel token in `secrets/cloudflare-tunnel-token.txt`, with access
   limited to the owner and Docker. Do not paste it into chat or commit it. The
   secrets directory is excluded from Git and Docker build contexts.
3. Run the offline preflight above. It checks configuration and token-file
   presence, not credential validity, DNS or public reachability.
4. After approval to activate, run:

   ```powershell
   docker compose --env-file .env.live -f compose.live.yaml -f compose.tunnel.yaml --profile tunnel up -d --no-deps tunnel
   ```

   This starts only the connector; the application must already be running. The
   optional overlay uses a version-pinned Cloudflare image and a token file, has
   no exposed host ports, and restarts with Docker. Docker Desktop itself still
   needs to start after host reboot; keep the host awake during the event.
5. Test the new HTTPS hostname from another device, including login and image
   loading, before retiring the current Quick Tunnel. Keep the old link running
   until the new route is verified. Disabling only the named connector leaves
   the existing app/database intact.

No DNS route, token, persistent connector or hostname is created by these local
changes. Public tunnel reachability must be checked externally.

Cloudflare references: [tunnel setup](https://developers.cloudflare.com/tunnel/get-started/),
[token-file parameters](https://developers.cloudflare.com/tunnel/reference/run-parameters/),
[Quick Tunnel limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
