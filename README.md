# Supervisely Monitoring App

A shared Supervisely web app for the monitoring team to oversee independent participant pairs. This is the first implementation of the Nightjar operational plan, ready for a technical pilot, with live Supervisely integration still to be tested against your instance.

**Event sizes come from your inputs.** No team, image or video count is hard-coded. Three independent teams per source is the plan's minimum replication rule, configurable upward in the allocation planner. Batch workload must be supplied explicitly from a pilot estimate; the app does not assume how many images take two hours.

## Implemented

- One native multi-user app session, personal browser filters and server-enforced assigned-team access.
- PostgreSQL persistence for assignments, batches, current review decisions and an append-only application audit history.
- Team dashboard, current and historical batches, confirmed image counts, complete-video submission status, recent participant labeling activity and stale-data warnings.
- Shared polling worker with a database lease, rather than one Supervisely polling loop per browser. Dashboard refresh every minute and manual selected-batch Supervisely refresh.
- Image and explicit video-frame review records; native image acceptance/rejection can be published separately.
- Sample approval and sequential release gates. Future jobs are created only on release, so a pending job cannot accidentally reveal a future batch.
- Optimistic concurrency checks for workflow changes and release reconciliation after ambiguous API results. Uncertain creates are never blindly retried.
- Input-driven allocation, validated import of already prepared teams/datasets, and monitoring-record export.

## Technical choices

The app runs in the **monitoring Supervisely team**. Each monitor must also have the required manager/reviewer permissions in every participant team assigned to them. Belonging to the monitoring team alone does not grant cross-team access. Monitor actions use that monitor's authenticated API credential; a separate server-side polling credential needs read access to all participating teams. The app verifies the token's user ID, monitoring-team membership and its own team assignment on requests.

The SDK supports native multi-user widget sessions. The UI's filters and form state live in each browser, while all decisions live in the database. Do not run a separate app per monitor or use process-global selected-team state.

Independent annotation versions must use **distinct team-local entities** with a stable mapping back to each original source. This implementation validates the mapping and team ownership but does not yet perform the dataset copies or establish how Supervisely charges storage for them.

Image batches produce non-overlapping jobs for the two participants. A whole video produces **one job for the pair's first listed annotator**, avoiding automatic splitting of the final video. Joint video editing, partner handover and how the second participant contributes must be settled in the Supervisely usability pilot before the event. Do not infer two independent annotation versions within a participant pair.

## Run a local synthetic demo

Python 3.12 is the tested interpreter. The demo creates fake data in its own database; it does not call Supervisely without configured credentials.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export LOCAL_DEVELOPMENT=true
export DATABASE_URL=sqlite:///demo.db
export MONITORING_TEAM_ID=1
export LOCAL_USER_ID=900
python -m monitoring.cli demo --teams 3
python -m monitoring.run
```

Open `http://127.0.0.1:8000`. The example count is synthetic and can be changed. To reset the demo, delete only its demo database and import again. Live integration buttons need credentials and real mapped entities.

### Windows PowerShell

Supervisely imports `python-magic`, whose normal pip package does not include the native Windows `libmagic` library. Install the Windows binary package **after** the main requirements. Both distributions provide the `magic` module, so the explicit second installation ensures the bundled Windows loader is used. Repeat that step if you reinstall or upgrade `python-magic` later.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
```

Create a private `.env` file in the repository root containing:

```dotenv
LOCAL_DEVELOPMENT=true
DATABASE_URL=sqlite:///demo.db
MONITORING_TEAM_ID=1
LOCAL_USER_ID=900
```

Then run:

```powershell
.\.venv\Scripts\python.exe -c "import magic; import supervisely; print('Windows dependencies loaded successfully')"
.\.venv\Scripts\python.exe -m monitoring.cli demo --teams 3
.\.venv\Scripts\python.exe -m monitoring.run
```

If the synthetic demo is already loaded, skip the `demo` command. The dependency fix and subsequent starts preserve the existing database and `.env` file. Open `http://127.0.0.1:8000` after the server starts.

Dependency references: [python-magic installation](https://github.com/ahupp/python-magic#installation) and [Windows binary wheels](https://pypi.org/project/python-magic-bin/0.4.14/).

## Prepare a real pilot

1. Make a monitoring team and add the monitors. Prepare participant pairs, their workspaces and independent annotation projects with the agreed annotation metadata and anatomical guide. Give each assigned monitor the required permissions in their participant teams.
2. Start persistent PostgreSQL. For a local database, set `POSTGRES_PASSWORD` and run `docker compose up -d database`. Configure `DATABASE_URL` with the database address reachable by the Supervisely agent. The example Compose port is loopback-only; it is not directly reachable from a remote Supervisely agent.
3. Copy `.env.example` to a private `.env`; set the monitoring team ID and credentials. Keep `LOCAL_DEVELOPMENT=false` in a hosted session. Use server-side secrets rather than committing tokens. Supervisely normally supplies the app session's `SERVER_ADDRESS` and `API_TOKEN`.
4. Build a private manifest in the format of `examples/manifest.json`, replacing every sample ID with actual IDs. The example intentionally contains one pair and is under-replicated. Dataset IDs are team-local; `source_id` identifies the same original across independent versions. Video `frames` must match the full video.
5. Run the commands below with an organiser credential authorised to inspect every configured team and dataset. Import performs read-only remote preflight and an atomic database import; it creates no remote labeling jobs.

```bash
python -m monitoring.cli init-db
python -m monitoring.cli validate local-manifest.json
python -m monitoring.cli import local-manifest.json --pilot
```

For the actual event, omit `--pilot`: every source in the manifest must appear in at least three distinct participant teams. Also compare manifest coverage with the complete source inventory, so a source omitted entirely cannot escape validation. Imports refuse to overwrite an existing event database.

6. Install this repository as a private Supervisely app in the monitoring team, following the [official private-app guide](https://developer.supervisely.com/app-development/basics/add-private-app). Configure its agent environment with the database connection and `SYNC_API_TOKEN`. The minimum native multi-user instance version is 6.15.2; the SDK is pinned in `requirements.txt`.
7. Launch one app session. Monitors open that shared session, inspect annotations through its Supervisely job links, record sample reviews, approve submitted batches and release the next task. Review notes are internal; image decision publication sends only native acceptance/rejection status.

Without `SYNC_API_TOKEN`, the UI still works, but Supervisely progress updates require manual refresh. Polling errors retain the last successful progress values and block approval; activity failures leave an old activity timestamp visible.

## Plan allocation from your actual inventory

Input JSON:

```json
{
  "team_ids": [101, 102, 103],
  "assets": [
    {"source_id": "image-a", "kind": "images", "units": 1},
    {"source_id": "video-a", "kind": "video", "units": 240}
  ]
}
```

These are sample IDs and workload values. `units` can represent frame counts or positive integer pilot-derived effort estimates, but use the same scale across image and video assets. The allocation is a preparation proposal, not a remote provisioning command.

```bash
python -m monitoring.cli plan inventory.json --replicas 3 --batch-units 120 --output allocation.json
```

The planner reports when the actual number of teams cannot satisfy one full video per team and the requested replication. If there are more teams than the minimum video replicas require, additional teams receive extra independent replicas. It balances total estimated load greedily; it does not guarantee identical workload or annotation difficulty. Resolve this proposal into a team-local entity manifest after storage/distribution is agreed.

## Recovery and export

If release times out after a remote job might have been created, its batch stays `releasing`. Use **Reconcile release**. This looks up the deterministic per-participant names and checks dataset, assignee, entity count and entity IDs before adopting all expected jobs. If no jobs or only some jobs are found, stop and investigate with the organiser; there is no automatic recreate or reset command. Confirm delayed requests are no longer in flight before manual repair.

```bash
python -m monitoring.cli export-records --output exports/monitoring-records.json
```

Create the output directory first. This exports source mappings, decisions and audit history. It does **not** collect annotation payloads or close native jobs. Keep the exported records private. Back up PostgreSQL throughout the pilot/event; app shutdown must not delete it. `create_all` initialises the initial schema only; use reviewed migrations before changing a populated schema.

## Verification

```bash
python -m pytest -q
node --check monitoring/static/dashboard.js
```

Set `TEST_DATABASE_URL` to a **disposable** PostgreSQL database to run the same workflow/concurrency tests there; tests drop their schema. GitHub Actions provisions its own PostgreSQL service. SDK signature tests check the installed pinned package, while gateway tests mock remote responses. Live API access, native two-user sessions, job links, cross-team permissions, correction workflows and account limits remain pilot checks. Concurrent-user capacity has not been load-tested.

## Next implementation work

See [the implementation roadmap](docs/implementation-roadmap.md) for account provisioning, storage-aware distribution, participant correction alerts, video collaboration, annotation validation, final collection and the event-scale pilot.
