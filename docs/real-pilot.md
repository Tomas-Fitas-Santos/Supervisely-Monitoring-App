# Real Supervisely pilot

Only monitors and the organiser open this app. Participants annotate and submit through their own Supervisely accounts. Demo and Pilot run this same live workflow with the bundled sample inputs; the separate Simulator is only an offline development tool.

## Start

On Windows, update the implementation branch and start the live demo (the --pilot alias uses the same workflow and database):

```powershell
git fetch origin
git switch feat/multi-user-monitoring-mvp
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
.\.venv\Scripts\python.exe -m monitoring.run --demo
```

Open http://127.0.0.1:8000 and connect the organiser account in the UI. A fresh installation needs no event `.env`. Demo and Pilot share `data/account-pilot/`. The ordinary local event (`data/`) and offline Simulator (`data/organiser-demo/`) stores are separate. Upgrading from the old offline Demo leaves its records untouched. Local mode represents one connected account, even with multiple browser tabs; genuine multi-user testing needs a shared native hosted session. `Start-App.ps1 -Mode Pilot -Install` is the Windows shortcut.

## Supplied account rehearsal

The CSV creates **TEAM 1** with `tomas.gaming.21@gmail.com` as Annotator and **TEAM 2** with `tomas.santos.work2002@gmail.com` as Annotator. Both use `tomas.bf.santos@gmail.com` as monitor/Manager responsibility. Each demo team therefore has two people in total, not two annotators. The organiser retains native Admin membership and is the assigned job reviewer.

1. Connect the token belonging to **tomas.bf.santos@gmail.com**. Confirm all accounts use this Supervisely instance. The organiser's identity comes from the token; participants do not provide API tokens/passwords.
2. Create/adopt the two event groups in **Monitors & Teams**. Then **Load bundled demo CSV → Create teams and invite CSV emails**. Both native teams appear before acceptance. No global account lookup or username collection is required for this path.
3. Inspect each member's status. Pending acceptance is normal and does not block other setup. Each actual annotator signs into Supervisely and accepts the invitation for its participant team. Click **Refresh all invitations**; both teams must become **Ready**. A user ID alone is not acceptance.
4. If the instance rejects email through the public invitation route, the created team remains visible with an error. Use **Open native invitations**, select the exact team name/ID shown by the app, invite that row's email in Supervisely, and refresh membership. See [invitation integration and limits](email-invitations.md). Inspect uncertain writes before acknowledging them; never blindly recreate resources.
5. In **Dataset**, click **Load bundled CC0 bird photos**, or upload the nested `examples/organiser-demo/dataset/` folder using the folder picker. It contains two photos and no video media. Non-media README files are skipped. The same basename in two source folders must remain distinct.
6. Select both source photo datasets and both ready teams. Use replication **1**, image effort **1** and batch effort **1** for **one unique image per team**, equal effort and no overlap. Use replication **2** if both teams should annotate both photos independently. Save/freeze the annotation guide/scoring rules, preview coverage/effort and apply distribution.
7. In **Monitoring**, select TEAM 1 and release its batch. Open the native labeling job as `tomas.gaming.21@gmail.com`, annotate and submit. Repeat with TEAM 2 and `tomas.santos.work2002@gmail.com`. The jobs must have the correct native annotator IDs, team-local entity IDs and your reviewer ID.
8. Refresh/capture in the monitor app and judge the images. Test correction/resubmission and native acceptance, then close and export this rehearsal. Keep its database for inspection. Use fresh production PostgreSQL for the actual competition; real resources are not reset/deleted automatically.

The presentation shortcut in offline `--simulator` mode explicitly simulates invitation acceptance and submission. Those controls are unavailable in real mode. If an older offline roster is loaded, reset it with a backup. If a previous real pilot already assigned these emails to another registration, retain that event and start this new rehearsal with separate storage/resources; do not silently reassign or delete its work.

## Prove the capabilities before full provisioning

| Check | Required observation |
|---|---|
| Instance/account identity | All accounts exist on the same instance as the organiser; registered emails resolve to the intended native IDs. |
| Email invitations | Public members.add accepts the CSV email, or the documented native UI fallback sends it. No instance-wide users.list access is needed. |
| Accepted identity | Invited/accepted filtering works; the invitation association yields the correct native ID even when profile email is hidden. Conflicting IDs/emails are rejected. |
| Invitations | Determine whether native membership is immediate or requires acceptance. Verify active membership before creating jobs. |
| Pair isolation | Each pair edits independent entities, cannot open competitors' jobs and cannot access unreleased work through its normal native role. |
| Native resources | Confirm team creation/adoption permissions and actual licence/seat/storage limits before scaling the roster. |
| Image task ownership | Both pair members receive disjoint image tasks; the app combines their submission state correctly. |
| Video ownership | Whole-video tasks load correctly, required frame indices align with the original media, and ownership can be handed over between pair members. |
| Submission capture | Observe submission, capture JSON/metadata and confirm the original version remains unchanged after a correction. Record real capture latency. |
| Reviewer handover | Transfer a released team, verify every native job reviewer changes, then verify former app assignment access is revoked. Previous native memberships are retained. |
| Native acceptance | Fully judge a small submitted batch, accept it and verify native job completion; changed annotations must fail pre-acceptance comparison. |
| Corrections | Return a specific image/frame issue, inspect the new native task instructions and ensure resubmission creates a new captured version without duplicate points. |
| Hosted users | Distinct monitors share one session with independent selections. Manager monitors cannot provision or enable organiser oversight. |
| Failures | Simulate interruption/timeout in provisioning or release and use the recorded resource IDs and inspection/reconciliation flow. Never blindly recreate uncertain resources. |

The app does not bypass invitation acceptance or inaccessible accounts. A failed/uncertain provisioning operation blocks further remote setup until an organiser inspects the actual resources and records the outcome.

## Agree the event rules

Enter the anatomical guide, bird identity convention, visibility/occlusion requirements and empty-frame convention. Decide whether every video frame is required or define a frame stride. Use source IDs for a common scored challenge when appropriate.

Measure annotation effort and monitor review time with representative material. Set source effort overrides and batch effort from that pilot; do not infer effort from file count alone. Preview actual replication, required units, source mix and effort spread. Review capacity must cover the chosen judging policy; a reviewed sample does not establish an individual score for every unreviewed frame.

Choose one official scoring/ranking policy and freeze it before judging. Define how the latest observed submission, corrections, unfinished released work and closure will be interpreted in the competition rules. The implemented scoring denominator consists of captured required units; unreleased future batches are excluded, and missing penalties require explicit judgments. There is no automatic anatomical ground truth or automatic penalty for not submitting a task.

## Shared deployment and polling

Deploy one multi-user Supervisely app session in the monitoring team, with persistent PostgreSQL configured by the deployment operator. Do not expose the SDK app server as an independent public service; use the platform's authenticated session gateway. Each action verifies native credential identity and app assignment.

Without a server polling credential, open monitor browsers refresh their own assigned teams through per-monitor leases. They do not poll teams outside their assignments. For unattended all-team polling/automatic observed closure, the deployment operator supplies a credential that has the required access to all participant teams. The organiser can always refresh and close explicitly from the app.

Automatic deadline closure is polling-based. It stops currently known jobs and captures final observed state; it cannot guarantee a single atomic state at an exact deadline instant. If strict competition rules require that guarantee, prove a server-enforced locking/versioning procedure on the actual instance before using it. Uncertain closure/acceptance/handover operations retain checkpoints and require inspection.

## Rehearsal and collection

Use actual monitor accounts and representative images/videos. Measure API latency, poll duration, database growth, frame-gallery load time, annotation capture size and review capacity. Rehearse concurrent browsers, app/database restart and recovery. Native multi-user support is not a capacity benchmark.

Download the event JSON and verify source mappings, captured annotation payloads, metadata, submission hashes/versions, assessments, correction lineage, rules, closure and audit. Confirm participants' annotation semantics with the scientific organisers. Keep real registrations and exports private; back up the database and encrypted-connection key. Do not delete a native participant team that contains event work.
