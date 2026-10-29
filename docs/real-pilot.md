# Real Supervisely pilot

Only monitors and the organiser open this app. Participants annotate and submit through their own Supervisely accounts. The bundled simulator is a usability demonstration; this checklist validates the actual platform integration.

## Start

On Windows, update the implementation branch and run without the demo flag:

```powershell
git fetch origin
git switch feat/multi-user-monitoring-mvp
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
.\.venv\Scripts\python.exe -m monitoring.run --pilot
```

Open http://127.0.0.1:8000 and connect the organiser account in the UI. A fresh installation needs no event `.env`. Pilot (`data/account-pilot/`), ordinary local event (`data/`) and organiser-demo (`data/organiser-demo/`) stores are separate. Local mode represents one connected account, even with multiple browser tabs; genuine multi-user testing needs a shared native hosted session. `Start-App.ps1 -Mode Pilot -Install` is the Windows shortcut.

## Supplied account rehearsal

1. Connect the token belonging to **tomas.bf.santos@gmail.com**. Confirm the server is the instance where all three accounts exist. The app obtains the actual native user ID/login from this token.
2. In **Monitors & Teams**, create or adopt the Monitors and Annotators groups. Add the connected organiser to Monitors using **Add monitor → Include my account → Save monitors**. The organiser must have native Admin permission in the chosen monitoring team.
3. Click **Load bundled demo CSV**, then **Match all registration emails**. The one pair is **tomas.gaming.21@gmail.com** and **tomas.santos.work2002@gmail.com**. Login columns are deliberately blank: email is not assumed to equal login. If necessary, enter each actual accessible native login and verify the email association explicitly. Select your monitor account and provision the pair.
4. Where native invitations require acceptance, sign into each participant account and accept them. Inspect operation history and actual resource IDs before clearing a blocked setup operation; do not blindly repeat partial creation.
5. In **Dataset**, click **Load bundled CC0 bird photos**. This uploads real media to the configured monitoring-team workspace. Select both photo datasets; replication 1, image effort 1 and batch effort 1 are suitable for this tiny rehearsal. Save/freeze your guide and scoring policy, preview and apply distribution.
6. In **Monitoring**, release the first upcoming batch. Open its native labeling job as the assigned participant in another browser profile. Annotate the image and submit through Supervisely. Return to the monitor app, refresh/capture and judge it. Native presence, submission and judgment remain separate.
7. Release the next batch after the first submission (or approval if that release gate was chosen). Its task belongs to the other participant. Annotate/submit using that account. This proves both accounts receive work despite the photos being in separate source folders.
8. Test correction/resubmission and native acceptance, then close and export this rehearsal. Keep the pilot database for inspection; use a fresh production PostgreSQL event for the competition. There is no automatic deletion/reset of real native resources.

These supplied emails are not hardcoded event authorization. The production app uses token identity, current native membership and app assignments. The demo role switch and simulated submission are unavailable here.

## Prove the capabilities before full provisioning

| Check | Required observation |
|---|---|
| Instance/account identity | All accounts exist on the same instance as the organiser; registered emails resolve to the intended native IDs. |
| Email visibility | Automatic matching succeeds with the actual permissions, or explicit accessible-login/manual verification resolves hidden email. A conflicting returned email is rejected. |
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
