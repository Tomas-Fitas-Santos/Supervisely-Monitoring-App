# Supervisely Monitoring App

A shared Supervisely web app for the monitoring team to oversee independent participant pairs. It includes a real pilot setup flow for registered users, source uploads, independent team copies and monitor assignment. Live API behavior must still be validated against your instance.

**Event sizes come from your inputs.** No team, image or video count is hard-coded. Three independent teams per source is the plan's minimum replication rule, configurable upward in the allocation planner. Batch workload must be supplied explicitly from a pilot estimate; the app does not assume how many images take two hours.

## Implemented

- One native multi-user app session, personal browser filters and server-enforced assigned-team access.
- PostgreSQL persistence for assignments, batches, current review decisions and an append-only application audit history.
- Team dashboard, current and historical batches, confirmed image counts, complete-video submission status, recent participant labeling activity and stale-data warnings.
- Shared polling worker with a database lease, rather than one Supervisely polling loop per browser. Dashboard refresh every minute and manual selected-batch Supervisely refresh.
- Image and explicit video-frame review records; native image acceptance/rejection can be published separately.
- Sample approval and sequential release gates. Future jobs are created only on release, so a pending job cannot accidentally reveal a future batch.
- Optimistic concurrency checks for workflow changes and release reconciliation after ambiguous API results. Uncertain creates are never blindly retried.
- Admin-only Event setup: create or register participant pairs with existing user logins, assign monitors and provision their participant-team permissions.
- Browser uploads of image/video files and optional Supervisely annotation metadata; existing monitoring-team datasets can also be selected.
- Preview and apply input-driven distribution into independent team-local entities, with durable operation checkpoints and source mappings. Small pilots can use reduced replication or images only.
- Supervisely SDK colors (blue primary, white panels and the platform neutral/status palette), validated manifest import and monitoring-record export.

## Technical choices

The app runs in the **monitoring Supervisely team**. Each monitor must also have the required manager/reviewer permissions in every participant team assigned to them. Belonging to the monitoring team alone does not grant cross-team access. Monitor actions use that monitor's authenticated API credential; a separate server-side polling credential needs read access to all participating teams. The app verifies the token's user ID, monitoring-team membership and its own team assignment on requests.

The SDK supports native multi-user widget sessions. The UI's filters and form state live in each browser, while all decisions live in the database. Do not run a separate app per monitor or use process-global selected-team state.

Independent annotation versions must use **distinct team-local entities** with a stable mapping back to each original source. Event setup performs the copies and validates team ownership, source inventory and entity mappings. Storage billing and quota behavior remain governed by your Supervisely instance.

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

## Prepare a real pilot in the app

See [the real pilot walkthrough](docs/real-pilot.md) for exact Windows commands and the hosted multi-monitor configuration.

Use a fresh `pilot.db` with your real monitoring-team ID and private `API_TOKEN`. Local live mode uses the token owner's identity. Open **Event setup** as an Admin of that monitoring team:

1. Create or register participant pairs using existing Supervisely logins, then assign their monitors.
2. Upload your image/video files, or select source datasets already in the monitoring team. Optional `meta.json` preserves your annotation schema; raw uploads otherwise use an editable bird-head pilot template.
3. Select actual teams/datasets, enter replication and workload estimates, and preview the allocation. Enable **Small pilot** for reduced replication or images-only work.
4. Apply the preview to create separate team-local annotation entities and locked batches.
5. Assigned monitors release real jobs, inspect submitted annotations, record reviews and advance through batches.

The local server is loopback-only and represents one token owner. For simultaneous real monitors, launch one shared Supervisely session with `LOCAL_DEVELOPMENT=false` and persistent PostgreSQL. Configure a separate `SYNC_API_TOKEN` for background polling, or refresh selected batches manually.

Setup operations have durable resource checkpoints and a database lock. Uncertain writes require inspection before new setup actions or releases; see the walkthrough's recovery procedure. Monitor reassignment is supported before job release; existing native reviewer jobs require a separate handover. The new setup tables are initialized automatically without deleting your existing demo or review records.

### Advanced: import already prepared datasets

The previous manifest workflow is still available if you already prepared independent participant-team projects. Build a private file using `examples/manifest.json`, replacing every sample ID, then run:

```bash
python -m monitoring.cli init-db
python -m monitoring.cli validate local-manifest.json
python -m monitoring.cli import local-manifest.json --pilot
```

This performs read-only remote preflight and an atomic initial database import. Omit `--pilot` for the full event: each source needs at least three independent teams and each team needs a final complete video. Imports refuse to overwrite an existing event database. Do not mix manifest import with an in-progress Event setup operation.

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
node --check monitoring/static/setup.js
```

Set `TEST_DATABASE_URL` to a **disposable** PostgreSQL database to run the same workflow/concurrency tests there; tests drop their schema. GitHub Actions provisions its own PostgreSQL service. SDK signature tests check the installed pinned package, while gateway tests mock remote responses. Live API access, native two-user sessions, job links, cross-team permissions, correction workflows and account limits remain pilot checks. Concurrent-user capacity has not been load-tested.

## Next implementation work

See [the implementation roadmap](docs/implementation-roadmap.md) for resumable setup recovery, participant correction alerts, video collaboration, annotation validation, final collection and the event-scale rehearsal.
