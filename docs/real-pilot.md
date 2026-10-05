# Test with real Supervisely users and data

You can now prepare the pilot in **Event setup**. No manifest file is needed. All counts come from your selected users, teams and actual files. A reduced pilot is an explicit option; it does not change the full-event replication rule.

## Prerequisites

- Registered Supervisely accounts for the organiser, monitors and annotators; no new passwords are created.
- Permission and available account quota to create native teams and projects, or Admin access to existing teams selected in the app.
- For simultaneous monitors, one shared hosted session with persistent PostgreSQL storage. Local testing represents the connected organiser.

**Only monitors and the monitoring organiser use this app.** Participating annotators use the actual Supervisely web app to open labeling jobs, annotate and submit. They do not need to open this monitoring app.

The app has two event rosters: **Monitors** and **Annotators**. It creates or adopts separate Supervisely teams for these groups and saves their IDs automatically. Each participant pair gets its own native team for independent annotations. A person belongs to only one event roster and each annotator belongs to at most one pair. The organiser retains administrative membership in native teams, separately from event roster membership.

Users visible in accessible teams appear as suggestions. You can also paste registered logins, one per line, for users outside those teams. Global user-listing privileges are not required.

## Windows: start and connect through the app

Stop the running app with Ctrl+C, then update and start:

```powershell
git switch feat/multi-user-monitoring-mvp
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
.\.venv\Scripts\python.exe -m monitoring.run
```

Open <http://127.0.0.1:8000>. On a fresh local installation no `.env` is needed. The app initializes `data/event.db`. Use **Connect to Supervisely** to enter your server address and organiser API token once. You can get the token from your Supervisely account settings. Then open **Event setup**. No monitoring-team ID, annotator IDs, monitor assignments or polling token need to be entered in environment files.

If you already have the synthetic demo configuration, the same connection screen starts a separate live event automatically and keeps the demo database. The app remembers the live database on restart. Existing real events retain their database and require the original organiser account/server. During the upgrade choose the original monitoring team in the group screen; existing paired people are brought into the appropriate rosters automatically.

The local credential is encrypted before database storage; the key lives in `data/credential.key`. Both are private local application data and excluded from git. The credential is never included in widget state, setup history or monitoring exports. Back up the database and key together. **Update connection** replaces an expired token for the same organiser and server. Local polling uses this saved connection automatically.

Local mode binds to loopback and represents one token owner. Multiple local tabs do not represent different authenticated monitors. Use the hosted session below to test simultaneous monitors with their own accounts.

## Prepare and run the pilot

1. **Create the groups.** Create or choose the Monitors and Annotators groups in Event setup. In a hosted session the Monitors group uses the app's launch team, so all monitors can open the same session. A source workspace is created automatically.
2. **Add people.** Add all monitors to Monitors and all participants to Annotators, using the user picker or registered logins. Use Include my account if the organiser will also monitor. The app adds native memberships and the required monitor roles.
3. **Create pairs of two.** Choose two unpaired members of Annotators and a team name. Create a native participant team or select an existing one where you are an Admin. Choose Assign later to prepare all pairs before allocating them.
4. **Distribute pairs to monitors.** Use the roster table for manual assignments, or select monitors and preview balanced assignments. The proposal considers existing pair counts, preserves assigned pairs and distributes only unassigned pairs. Review and apply it. The app grants Manager permissions in each assigned participant team. Reassignment is supported **before jobs are released**.
5. **Upload source data.** Select a monitoring-team workspace and a source project name. Upload image files, then videos separately if you want to test the final-video phase. You can also select existing image/video datasets already in the monitoring team; click Reload Supervisely after external changes. The uploader handles raw media files, not dataset ZIP archives. Import other dataset formats into Supervisely first, then select them here.
6. **Choose annotation metadata.** The optional `meta.json` input accepts an existing Supervisely schema. Without it, uploads use an editable bird-head pilot template: a `bird` rectangle, four separate point classes (`crown`, `left_eye`, `right_eye`, `beak`), a shared `bird_id` object tag and point `visibility` values `visible`/`occluded`. Use the same bird_id on one bird's box and points. This is a usable pilot schema, not a claim that it is the final scientific annotation protocol. You can edit source classes/tags in Supervisely before distribution. Existing dataset metadata is retained.
7. **Preview distribution.** Select actual source datasets and participant teams that have monitors and no batches yet. Enable Small pilot to allow one or more replicas and an images-only rehearsal. Enter the independent teams per source, image effort units and maximum image-batch effort units. These are your estimates; there is no assumed image throughput. Videos use real frame counts as effort units and remain whole. When videos are included, there must be enough teams to cover every video at the requested replication, with at most one final video per team. Extra teams receive additional complete-video replicas. The planner rejects teams with no work.
8. **Apply the preview.** Check the per-team image, video and batch counts and the monitor assignments. Apply creates separate projects/datasets and distinct annotation entities in participant teams. Existing labels are copied only if you selected Copy existing source annotations; otherwise copies start blank. Source mappings and locked batches are committed only after all copies succeed. SDK batch copying can share underlying media; storage billing/quota behavior remains governed by your instance.
9. **Release the first task.** Open Monitoring as the assigned monitor. Click Release task. Annotators open their real Supervisely labeling jobs and annotate/submit them. The pair's image jobs use disjoint images. If a batch contains only one image, only one annotator gets a job. A complete video goes to the pair's first listed annotator; joint video handover is not yet implemented.
10. **Review and advance.** Refresh Supervisely, inspect the job, save a sample review and resolve correction flags. Approve a submitted batch, then release the next. Local background polling uses the saved organiser connection. Hosted monitors can refresh progress manually; a deployment operator may configure an optional server polling credential with access to all participant teams.

## Simultaneous real monitors

Deploy one shared Supervisely app session in the team that will host your monitors, with persistent PostgreSQL. Hosted mode is detected from the platform task context; do not enable local mode there. The platform supplies each monitor's own session credential. Follow the [private-app installation guide](https://developer.supervisely.com/app-development/basics/add-private-app). The deployment operator configures persistent database access once, plus an optional polling credential. This infrastructure is separate from event setup: the organiser configures all groups, people, pairs, assignments and datasets in the app. Hosted authentication uses each user's Supervisely session, with no connection-token form.

- Every monitor opens the same app session and sees only their assigned participant teams. Native monitoring-team membership alone does not bypass the event Monitors roster; Admins retain setup access.
- Admins can additionally open Event setup; Manager monitors cannot provision or inspect the global setup roster.
- A monitor must have the required participant-team role as well as an app assignment. Setup grants the selected monitor Manager permissions there.
- Changing an assignment removes the former monitor's app access to that pair, but does not remove their Supervisely team membership.
- Reassignment after job release requires a native reviewer handover and is intentionally rejected by the current assignment action.
- Use one app server process for the pilot. If scaling to multiple processes, `UPLOAD_STAGING_DIR` must refer to shared persistent storage. Each browser keeps its filters and setup selections locally; actions read their own request payload and use database concurrency checks.

## Interrupted setup operations

The setup database records operation IDs and confirmed remote resource IDs. It serializes provisioning across organisers. A failed or uncertain remote write blocks new setup operations and task releases; it is not automatically repeated.

Refresh Event setup and inspect **Setup operation history**. Check the listed teams/workspaces/projects/datasets in Supervisely and any operation still running. Resolve partial resources yourself, then record an inspection note with **Record inspection and unblock setup**. Acknowledging does not repair or retry the operation. A failed distribution has no published monitoring batches; remove its unused destination resources before preparing another preview. A registered team should not be deleted if it has confirmed batches.

If the process is terminated while an operation is applying, its persistent lock remains. Stop all app processes, verify no remote write is still in flight and inspect resources before database recovery. For this initial pilot, the operator can mark the interrupted `setup_operations.state` as `needs_inspection`, then restart and use the inspection action. Do not clear an active operation's lock. Automatic recovery and a background setup queue are follow-up work.

Browser files are transferred as 4 MiB JSON chunks through the authenticated SDK request path. `UPLOAD_MAX_FILE_MB` defaults to 1024 MiB per file and can be configured. Temporary successful uploads are removed after Supervisely confirms them; abandoned or failed staging files remain for inspection and may be cleaned when no upload/operation is running. Back up PostgreSQL before app updates; this update adds event configuration and membership tables without changing existing review columns.

## What has been checked

Automated tests cover saved encrypted connections, owner/server pinning, group membership, unassigned pairs, balanced monitor plans, group upgrades, setup authorization, token-derived local identity, request-local action state, independent entity mapping, image-only pilot allocation, full-event replication enforcement, monitor reassignment and stale previews, chunk ownership/offsets, source-team boundaries, failed copies, duplicate application and concurrent setup locks. The installed SDK signatures and pilot metadata are checked directly. Remote API responses are mocked: credentials for your live instance have not been supplied to this development environment. Validate account limits, roles, jobs and native multi-user sessions in your pilot before scaling up.

API references: [user management](https://developer.supervisely.com/advanced-user-guide/automate-with-python-sdk-and-api/user-management), [UserApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.user_api.UserApi.html), [ImageApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.image_api.ImageApi.html), [VideoApi](https://supervisely.readthedocs.io/v6.74.38/sdk/supervisely.api.video.video_api.VideoApi.html).
