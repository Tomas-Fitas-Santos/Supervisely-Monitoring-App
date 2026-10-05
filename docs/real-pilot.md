# Test with real Supervisely users and data

You can now prepare the pilot in **Event setup**. No manifest file is needed. All counts come from your selected users, teams and actual files. A reduced pilot is an explicit option; it does not change the full-event replication rule.

## Prerequisites

- A real Supervisely monitoring team. Your API token's user must have the **Admin** role there to use Event setup.
- A workspace in that monitoring team (the default workspace is fine).
- Registered accounts for the annotators. Enter their exact Supervisely logins. The app does not create accounts or passwords.
- Each monitor must already belong to the monitoring team with the Admin or Manager role. The organiser can also be a monitor, provided they are not one of that pair's annotators.
- Permission and available account quota to create participant teams/projects, or Admin access to existing participant teams you choose to register.

You do not need to prepare participant workspaces or independent project copies: distribution creates them. Users visible in teams you can access appear as suggestions; exact login entry also supports registered users outside those teams, through `add_to_team_by_login`. Global user listing is not required.

## Windows: local pilot against the real API

Stop the app with Ctrl+C. Pull the implementation branch and install requirements:

```powershell
git switch feat/multi-user-monitoring-mvp
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
```

Edit your private `.env` in the repository root:

```dotenv
LOCAL_DEVELOPMENT=true
DATABASE_URL=sqlite:///pilot.db
SERVER_ADDRESS=https://app.supervisely.com
MONITORING_TEAM_ID=YOUR_REAL_MONITORING_TEAM_ID
API_TOKEN=YOUR_PRIVATE_SUPERVISELY_API_TOKEN
SYNC_API_TOKEN=
```

Replace both placeholders; use your actual server address if you use a private Supervisely instance. Find the team ID in Supervisely's team context menu and your API token in your account settings. Keep the token private. Local live mode derives your identity from the token and ignores the old synthetic `LOCAL_USER_ID`.

**Use a new `pilot.db`, not the existing `demo.db`.** Do not run the `demo` or `import` command for this workflow. The app initializes the new database itself. Your old demo database stays available.

```powershell
.\.venv\Scripts\python.exe -m monitoring.run
```

Open <http://127.0.0.1:8000>. An Admin in the configured monitoring team sees the **Event setup** tab after the first refresh. If the tab is absent, check the team ID, token owner's membership and Admin role.

This local server is bound to loopback and uses one token owner's identity. It lets you test real provisioning, uploads, jobs and annotations. Multiple tabs here do not represent different authenticated monitors. For simultaneous monitors, use the shared hosted session described below.

## Prepare and run the pilot

1. **Register a pair.** Enter a participant team name and two registered user logins. Choose its monitor. Create a new Supervisely team, or select an existing participant team where you are an Admin. The app adds missing annotators and gives the assigned monitor Manager permissions. Repeat for as many pairs as you want to test. The roster table lets you change monitor assignments **before jobs are released**.
2. **Upload source data.** Select a monitoring-team workspace and a source project name. Upload image files, then videos separately if you want to test the final-video phase. You can also select existing image/video datasets already in the monitoring team; click Reload Supervisely after external changes. The uploader handles raw media files, not dataset ZIP archives. Import other dataset formats into Supervisely first, then select them here.
3. **Choose annotation metadata.** The optional `meta.json` input accepts an existing Supervisely schema. Without it, uploads use an editable bird-head pilot template: a `bird` rectangle, four separate point classes (`crown`, `left_eye`, `right_eye`, `beak`), a shared `bird_id` object tag and point `visibility` values `visible`/`occluded`. Use the same bird_id on one bird's box and points. This is a usable pilot schema, not a claim that it is the final scientific annotation protocol. You can edit source classes/tags in Supervisely before distribution. Existing dataset metadata is retained.
4. **Preview distribution.** Select actual source datasets and participant teams that have no batches yet. Enable Small pilot to allow one or more replicas and an images-only rehearsal. Enter the independent teams per source, image effort units and maximum image-batch effort units. These are your estimates; there is no assumed image throughput. Videos use real frame counts as effort units and remain whole. When videos are included, there must be enough teams to cover every video at the requested replication, with at most one final video per team. Extra teams receive additional complete-video replicas. The planner rejects teams with no work.
5. **Apply the preview.** Check the per-team image, video and batch counts and the monitor assignments. Apply creates separate projects/datasets and distinct annotation entities in participant teams. Existing labels are copied only if you selected Copy existing source annotations; otherwise copies start blank. Source mappings and locked batches are committed only after all copies succeed. SDK batch copying can share underlying media; storage billing/quota behavior remains governed by your instance.
6. **Release the first task.** Open Monitoring as the assigned monitor. Click Release task. Annotators open their real Supervisely labeling jobs and annotate/submit them. The pair's image jobs use disjoint images. If a batch contains only one image, only one annotator gets a job. A complete video goes to the pair's first listed annotator; joint video handover is not yet implemented.
7. **Review and advance.** Refresh Supervisely, inspect the job, save a sample review and resolve correction flags. Approve a submitted batch, then release the next. With `SYNC_API_TOKEN` blank, progress refresh is manual; you may configure a separate read credential with access to all participant teams for background polling.

## Simultaneous real monitors

Deploy one shared Supervisely app session in the **monitoring team**, with `LOCAL_DEVELOPMENT=false` and persistent PostgreSQL. The platform supplies each monitor's own session credential. Follow the [private-app installation guide](https://developer.supervisely.com/app-development/basics/add-private-app). Use the agent's secret configuration for the database and polling token.

- Every monitor opens the same app session and sees only their assigned participant teams.
- Admins can additionally open Event setup; Manager monitors cannot provision or inspect the global setup roster.
- A monitor must have the required participant-team role as well as an app assignment. Setup grants the selected monitor Manager permissions there.
- Changing an assignment removes the former monitor's app access to that pair, but does not remove their Supervisely team membership.
- Reassignment after job release requires a native reviewer handover and is intentionally rejected by the current assignment action.
- Use one app server process for the pilot. If scaling to multiple processes, `UPLOAD_STAGING_DIR` must refer to shared persistent storage. Each browser keeps its filters and setup selections locally; actions read their own request payload and use database concurrency checks.

## Interrupted setup operations

The setup database records operation IDs and confirmed remote resource IDs. It serializes provisioning across organisers. A failed or uncertain remote write blocks new setup operations and task releases; it is not automatically repeated.

Refresh Event setup and inspect **Setup operation history**. Check the listed teams/workspaces/projects/datasets in Supervisely and any operation still running. Resolve partial resources yourself, then record an inspection note with **Record inspection and unblock setup**. Acknowledging does not repair or retry the operation. A failed distribution has no published monitoring batches; remove its unused destination resources before preparing another preview. A registered team should not be deleted if it has confirmed batches.

If the process is terminated while an operation is applying, its persistent lock remains. Stop all app processes, verify no remote write is still in flight and inspect resources before database recovery. For this initial pilot, the operator can mark the interrupted `setup_operations.state` as `needs_inspection`, then restart and use the inspection action. Do not clear an active operation's lock. Automatic recovery and a background setup queue are follow-up work.

Browser files are transferred as 4 MiB JSON chunks through the authenticated SDK request path. `UPLOAD_MAX_FILE_MB` defaults to 1024 MiB per file and can be configured. Temporary successful uploads are removed after Supervisely confirms them; abandoned or failed staging files remain for inspection and may be cleaned when no upload/operation is running. Back up PostgreSQL before app updates; this update adds new tables without changing existing review columns.

## What has been checked

Automated tests cover setup authorization, token-derived local identity, request-local action state, independent entity mapping, image-only pilot allocation, full-event replication enforcement, monitor reassignment and stale previews, chunk ownership/offsets, source-team boundaries, failed copies, duplicate application and concurrent setup locks. The installed SDK signatures and pilot metadata are checked directly. Remote API responses are mocked: credentials for your live instance have not been supplied to this development environment. Validate account limits, roles, jobs and native multi-user sessions in your pilot before scaling up.

API references: [user management](https://developer.supervisely.com/advanced-user-guide/automate-with-python-sdk-and-api/user-management), [UserApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.user_api.UserApi.html), [ImageApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.image_api.ImageApi.html), [VideoApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.video.video_api.VideoApi.html).
