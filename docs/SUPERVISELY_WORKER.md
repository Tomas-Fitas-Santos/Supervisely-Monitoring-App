# Supervisely worker contract and local runbook

## Contract implemented

Nightjar handles each **pair** as one local `LabelingJob` (one queue position,
one frame set, one review status). On explicit organiser launch, a durable
`NativeOperation` is created for the team's first job. A separate optional
Python worker uses the documented `supervisely` SDK, never a user browser token.

The SDK `api.labeling_job.create(name, dataset_id, user_ids, images_ids)`
returns a list of **one native job per assigned annotator**. Nightjar stores both
`NativeJobBinding` IDs; it never invents one shared native job ID. The two
native records reference a private *pair-job* image dataset. Different pair jobs
receive independent uploaded image objects, even when the same original file
appears in multiple jobs: this is essential to preserve overlap provenance.
Video frames are uploaded as independent image assets but a source video's
frames are never split across two local pair jobs.

**Unverified behavior:** whether two accounts editing two native jobs against
the same dataset produce one shared answer safely, or potentially overwrite
each other, MUST be established with an actual two-account acceptance test.
The SDK documentation does not guarantee cooperative concurrent editing.
Do not claim this has been proven by CI.

## Local configuration

Keep the existing `.env` private. Set a stable `NIGHTJAR_ENCRYPTION_KEY` and save the shared Supervisely admin/manager token through organiser Settings for the
organiser account. In Settings, configure both the Supervisely team and its
source workspace and use **Verify connection**. Refresh the native membership
and explicitly match each pair member to distinct, verified `annotator` IDs.
Create an ordinary local dataset, verify file inventory, create the frame-balanced
plan, then **Approve**. Native writes do not happen during these steps.

To enable the separate worker, install the optional SDK from the repository
root:

```powershell
python -m pip install -e ".[native]"
```

When ready to publish to the actual Pro team, choose **Launch event** in
**Competition & Jobs**. Start the worker in a third PowerShell terminal:

```powershell
.\.venv-nightjar\Scripts\Activate.ps1
python -m annotatiathon.native_worker --loop --poll-seconds 12
```

Alternatively use `--once` for one publishing task and one sync. No Docker
container or external scheduler is needed locally. Do not run the worker when
you only want local, offline validation.

## SDK methods used

The worker calls `sly.Api(server_address, token)`,
`api.workspace.get_info_by_id`, `api.project.get_list/create/get_info_by_id`,
`api.dataset.get_list/create/get_info_by_id`, `api.image.get_list/upload_paths`,
`api.labeling_job.get_list/create/get_info_by_id`. The worker never invokes
`delete`, member invitation/removal, role mutation, or remote status changes.

The importer calculates `MediaAsset.sha256` for provenance, and the publisher
saves the stable `NativeMediaInstance` mapping between source frame IDs and
Supervisely image IDs. The project name uses a stable Nightjar dataset UUID,
and each native dataset uses a local pair job UUID. Image names include
source media UUIDs; `upload_paths(conflict_resolution="skip")` prevents repeated
upload on retry. Before creation, the worker lists existing images and
labeling jobs. Any conflicting/ambiguous mapping produces an `attention`
state, not an automatic destructive repair.

## Release and status rules

- A confirmed organiser launches one first job per team.
- A pending task is claimed and committed before remote writes. Its state
  becomes `running`, then `done`; uncertain failures become `attention`.
- Each job has two native IDs. The worker polls each with
  `api.labeling_job.get_info_by_id`, recording status and sync timestamp.
- Both native statuses must reach `on_review` or `completed`, **and**
  `finished_images_count >= images_count`, before the local job is
  considered submitted. Incomplete or stopped jobs are returned for correction.
- On successful submission, the worker queues the next job for that team,
  even while sampled local review continues, unless releases are paused.
- Nightjar per-frame quality decisions do not forge native annotation
  completion or change remote reviewer decisions.
- Pause affects only future publishes. Already-created native jobs remain
  accessible on Supervisely.

## Recovery

A remote write can finish even when the API response is lost. Such tasks stay
in `attention` until an organiser inspects remote project/dataset/job records
and explicitly chooses Retry. Retry does not discard local media mappings.
Do not delete PostgreSQL records or native projects to clear a task.
Any process killed while an operation remains `running` needs operator
inspection before resetting that operation; automatic replay is intentionally
disabled.

Run nightly local PostgreSQL backups and preserve the `.nightjar-data`
directory together. Files are not stored inside PostgreSQL. Test restoring
both before event day.

## Validation status

Automated tests use a contract-compatible fake SDK to verify initial release,
pair-specific media copies, idempotent replay after an uncertain remote
job-creation error, pause/resume, and release of the next job only after
both annotators submit. **No live Supervisely account is contacted by CI.**

Before production deployment, test using **two real volunteer accounts**
and the actual team/workspace: invitation status, assigned roles, editing
concurrency, image isolation, read-only monitor access, resubmission after
rejection, real native annotation export, low-bandwidth recovery, and
storage/throughput at competition scale. This is an acceptance gate, not
a deployment-only checkbox.

## Read-only native annotation export

The **Results & Exports** screen can enqueue one final `export_event` operation
for an archived event. The worker uses the documented
`api.annotation.download_json(native_image_id)` method, never modifying the
native jobs. It creates an atomic ZIP with full provenance and accepted-only
curated manifests. Large exports remain in `LOCAL_DATA_DIR/exports/` until
downloaded. They contain annotation JSON only, not the original multi-GB media
or historical annotation version records. Ensure the source media directory
and database are preserved together after event closeout.

## Strict one-pair-job-at-a-time queue and submission decisions

An approved plan contains the planned distribution. **Only the first
pair-level job of each team** is published at event launch. One Nightjar job
is represented by two SDK-native job rows, one for each assigned annotator,
against the same job-specific image dataset. The SDK does not guarantee
simultaneous cooperative editing of a single annotation; this is a live
acceptance-test item.

The worker polls both native records. A pair goes **on review** only once
both records report submitted/reviewed state and every assigned image is
finished. A monitor scores at least one frame and explicitly accepts or
returns the job. Only `completed` with `review_status=accepted` releases
the next job for the same team. One native submission never releases work.

**Return for correction** holds the team on that logical job, creates a
durable correction operation, and uses the documented
`api.labeling_job.restart(..., only_rejected_entities=False)` method for
each member. The new IDs become the active bindings; the previous IDs stay
in `native_job_revisions`. Earlier sampled frame reviews remain visible,
and bad/incomplete frames must be rescored before acceptance. Uncertain
remote restarts require explicit operator retry and reconcile deterministic
remote job names rather than blindly creating duplicates. Pausing future
release does not block correction of an active job.

## Two-monitor local competition pairs

In **People & Teams → Monitor test pairs**, organisers can combine exactly
two active monitor records in one local competition team. Each keeps their
staff monitor role and retains their existing Supervisely Manager/Admin/
Developer role. The extra membership lives in `monitor_pair_members`;
the ordinary participant team field is unchanged. Planning supports these
pairs, and remote publication still requires both accounts to have verified
membership in the same native Supervisely team.

A pair can be disbanded before job planning, or after every job has been explicitly accepted and all native operations have settled.
Disbanding deactivates the pair membership and team, and deactivates local
monitor responsibility assignments. It does not delete people, native
membership, annotation jobs or event audit history. For tests that generated
jobs, create a new event rather than erasing provenance.

## Dataset upload recovery and cleanup

Dataset media are imported to the Nightjar private local disk before any
Supervisely labeling job is published. Uploading never silently publishes
media to Supervisely. The browser transfer coordinator runs outside the
Dataset page component, so moving between Nightjar tabs does not cancel it.

If the browser is reloaded, closed, or loses network access, the browser's
`File` references are not retained. After five minutes without accepted
chunks, an unfinished dataset displays as `interrupted`. Reselect the **same**
ZIP or folder and click **Resume with selected files**; the uploader checks
stored file paths, lengths and byte offsets to avoid re-sending accepted chunks.
Resuming requires the original files; it cannot fabricate missing bytes.

The organiser can **Stop upload** and **Delete incomplete dataset**. Delete
removes the dataset row, its staged-file rows and its partial private directory,
but refuses deletion of completed imports, imports with any planned jobs/media
inventory, or imports with Supervisely IDs. It does not delete any remote
Supervisely projects, datasets, images or annotations. Records from older
browser sessions that remain `uploading` are treated as interrupted after
the inactivity timeout.

`0014_dataset_upload_recovery` adds `last_activity_at` to the SQL table;
run Alembic migrations before starting the updated web API. Transfers continue
when moving between pages of one open browser tab; guaranteed continuation
after closing the browser would require a server-side source such as a
server-local upload directory or a separate trusted desktop uploader.
