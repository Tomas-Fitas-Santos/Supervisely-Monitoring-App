# One-button competition closeout implementation plan

**Goal** — Close scoring intake with one organiser action, finish processing the submissions Nightjar already admitted, then freeze the leaderboard. Supervisely remains the source for a separate dataset download that may include late work.

**Cutoff rule** — A submission counts if Nightjar committed it to its validation queue before the close transaction committed. Clicking Submit in Supervisely and being admitted by Nightjar are different events. The organiser can announce pens down, allow a short manual grace period based on a measured full polling cycle, then click **Close competition**. There is no timer or second close button. The UI shows the recorded cutoff time. Submissions admitted during the grace period count.

## Expected behaviour

| State | Submission intake | Assignments | Validation and scoring |
| --- | --- | --- | --- |
| Open | Normal polling and admission | Existing rules | Normal validation and sampled review |
| Finalizing | Closed immediately | No new primary, correction or manual assignment publication | Drain admitted submissions, then freeze scores |
| Archived | Closed | Closed | Serve frozen results and keep export access |

- A worker validates a queued submission even if it had not started when the organiser clicked. If 15 of 30 frames are valid, those 15 enter the review pool and the remaining frames produce no correction assignment.
- An open Supervisely job can still be edited or submitted there. An unadmitted submission never enters the competition after close, even if Supervisely received it just before the click.
- **Proposed review rule** — Keep sampled review available during the drain. Include only eligible monitor decisions committed before the final results transaction. Finish automatically when validation drains. Show monitors that the event is finalizing and an open review may miss the freeze. There is no final audit, quota, or requirement to review every frame.
- **Proposed zero-review rule** — Allow the event to archive with zero reviews. Record that there is no verified winner and keep the dataset accessible. Zero reviews alone must not block closeout.
- A transient failure keeps the event finalizing and retries the same admitted submission. A permanent or uncertain failure appears in the organiser view with a retry action and a reason. Do not silently discard it or freeze a partial leaderboard. Closing twice returns the original cutoff and current progress.

## 1. Add durable admission and a closeout state

**Files** — `src/annotatiathon/models.py`, `events_api.py`, `closeout_api.py`, and a new Alembic migration.

- [ ] Add a durable finalizing state and cutoff timestamp. Record each admitted validation by event, Nightjar job, correction round and exact Supervisely binding or revision. Reuse the durable validation task if the worker upgrade lands first. Otherwise persist this identity alongside the current worker flow and have the worker consume it. Do not use a process-local list or infer admission from a generic job status.
- [ ] Commit admission and the status transition together. Serialize admission and close on the same event row using short database transactions. Recheck eligibility after acquiring the lock. If a poll started before close and its response arrives after close, the database rejects admission. A committed admission survives a restart.
- [ ] Keep the existing organiser confirmation and permissions on `POST /closeout/finalize`. Change the endpoint to start finalization and return progress instead of archiving immediately. Repeated requests must return the original finalization state. No review-count or pending-sync gate on starting the drain.
- [ ] Migrate a populated copy. Preserve archived events and existing snapshots. Seed outstanding admissions only from reliable persisted submission evidence, including the relevant round and binding. Reconcile ambiguous historical rows before enabling closeout. Never enroll all in-progress jobs by default.

## 2. Stop assignment publication and drain only admitted work

**Files** — `native_worker.py`, `native_operations_api.py`, `work_queue.py`, `frame_submission.py`, and the relevant manual assignment and check-in entry points.

- [ ] Apply the closing gate at allocation and publication, including late check-in dispatch, manual publication, correction and reassignment. Leave pending outbound operations blocked and auditable. An already-sent remote create can still succeed. Record and reconcile that result without sending a second create. The job it created cannot introduce new competition submissions.
- [ ] Split active polling from validation during finalization. Do not scan Supervisely to discover extra submissions for a closing event. Process only the durable admitted identities, including work already running. Remove any extra legacy snapshot selection path that could introduce post-close frames.
- [ ] Save eligible complete frames idempotently and settle the validation task in the same database transaction. For partial jobs record the missing frame count, preserve eligible accepted frames for review and skip fresh or correction dispatch. Retry the exact admitted revision. A failed capture must remain visible and recoverable after a restart.
- [ ] Verify what Supervisely returns for an admitted job that an administrator reopens or changes before validation. Bind validation to the admitted revision. If the remote API cannot guarantee a stable snapshot and the job changed, show it as needing attention rather than silently importing newer work. This design closes **submission intake**, and does not enforce a remote editing lock or promise a point-in-time copy of mutable remote JSON.

## 3. Freeze results automatically and update the organiser UI

**Files** — `closeout_api.py`, `review_queue_api.py`, the ranking read path, and `web/src/closeout.tsx`.

- [ ] After the last admitted validation settles, have the worker finalize automatically. Take the same event lock used for admission and review completion. Check that no admitted tasks remain queued, running or unresolved. Compute and store the final report and leaderboard in the archive transaction. Make this idempotent, including after a restart between drain and archive.
- [ ] Keep review claims and completions available during finalization under the existing review permissions. Serialize completion against the freeze so that a verdict either makes the archived result or receives a clear event-closed response. Serve archived rankings from the stored results, including both leaderboard views. Preserve existing proportional sampling and points rules.
- [ ] Replace **Archive & finalize event** with **Close competition**. Show a confirmation explaining the cutoff. After the click, display the cutoff time and counts for queued, processing, completed and attention-required submissions. Allow retry of failed admitted work through the existing operation controls or an equivalent organiser action. Transition to archived status automatically. Show an explicit no-verified-winner state if no eligible reviews were completed.
- [ ] Keep the existing Nightjar JSON export separate from finalization. Document that organisers may download the dataset in Supervisely, including later saved annotations, and that those files cannot change archived Nightjar standings.

## 4. Verify before rollout

- [ ] On disposable PostgreSQL, race close against two polls, allocation, publication and review completion. Assert that pre-close admissions drain once, post-close admissions are rejected, and already-sent remote writes are recorded without another create.
- [ ] Exercise queued and in-flight submissions, a valid 15/30 partial, an unsubmitted job, a submission first detected after close, zero admitted tasks, zero reviews, duplicate clicks, sync failure and retry, worker restart, and a remote revision change. Confirm no duplicate score rows or follow-up assignments.
- [ ] Run a burst rehearsal with late submissions and concurrent monitor reviews. Check the organiser can see progress throughout and that archived standings stay fixed even after later changes in Supervisely. Measure a full detection cycle under load to guide the manual grace period.
- [ ] Run the populated migration, PostgreSQL concurrency suite, backend tests and frontend build. In a disposable Supervisely workspace, verify remote job and annotation behaviour with an annotator account. Do not test the close button on the live competition as a rehearsal.

This plan covers the closeout behaviour independently of worker count. The worker throughput upgrade can supply the durable validation queue, but both changes must use the same admission boundary and drain rules.
