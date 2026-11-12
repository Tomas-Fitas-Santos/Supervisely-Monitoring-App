# Check-in, tables, and shared job dispatch

## Decision

An active Nightjar competition team has a name and one or two active participants. Each participant must have a verified, joined Supervisely identity before check-in. Check-in records arrival only. Table assignment is a separate team operation. The shared queue publishes no jobs before the organiser launches it; after launch, each checked-in, eligible person gets a job when a block is available, including late arrivals. One checked-in member may work while their teammate has not arrived.

## Current gaps

- `pair_members` accepts exactly two people; plan creation, publication and queue claims rely on it.
- Attendance requires or sets a table at first check-in and clears it after the last checkout.
- Shared queue dispatch checks native identity and team but not attendance.
- Job Distribution displays job inventory, without a live team/member readiness list and per-person assignment sequence.
- The dual-developer plan and product spec describe the old table-at-check-in and two-person-only rules; update them with Tomas before implementation across his frontend/attendance ownership.

## Implementation sequence

### 1. Team and identity eligibility

- Add a competition-team member lookup that accepts one or two active participants. Keep monitor trial pairs explicit and separate. Reject empty teams, more than two participants, mixed member types, inactive teams and duplicate Supervisely IDs.
- Use this lookup in shared plan creation, launch validation, claims and native publication. Audit manual job paths separately so a solo participant can be assigned there too.
- Keep existing jobs and ownership stable if a team changes after publication; block new claims when current membership is invalid.

### 2. Independent check-in

- Check-in requires an active named Nightjar team and a verified joined identity in the configured Supervisely team. Return clear errors for each missing condition. Do not require the partner to be present or a table number.
- Add a `Supervisely ID` column to the check-in list. Show the person's linked numeric ID when present; show **None** in red when no ID is linked. If an ID exists but membership is not verified, display the ID with a separate `Not verified` status so staff do not mistake an ID for job readiness.
- Remove table input from check-in request and the check-in dialog. Preserve idempotent check-in/out and audit records. Checkout does not cancel a live Supervisely job.
- Stop clearing a team table at checkout. Tables represent seating assignments until staff changes them, even if a teammate temporarily leaves.

### 3. Table Assignment tab

- Add a separate tab for teams with at least one checked-in participant. Show team name, checked-in members, table number, and unassigned status. Allow authorised staff to set, change or clear the table for the whole team.
- Validate positive table numbers and prevent two active teams from silently sharing one table; provide an explicit resolution path for corrections. Update every team member's displayed table from the team record.
- Keep attendance check-in one click, independent of this tab.

### 4. Shared queue gate and late arrivals

- In the atomic claim transaction, require `check_in_at` for the selected person. Recheck identity, active team, and running plan under the existing locks so a stale worker read cannot publish an unchecked-in assignment.
- In worker selection, include only checked-in eligible people. Continue scanning while the plan is running, so a late check-in gets a first job on a later poll. At launch, the worker can assign jobs to all currently checked-in members.
- Preserve the existing one-open-packet-per-person rule. After a complete submission, dispatch the next available block to that person; an incomplete/correction job must still block a new assignment.
- Define checkout behavior for future assignments: stop new claims after checkout; keep an existing job visible for staff resolution.

### 5. Team-by-team readiness and progress

- Add a paginated, bounded-query endpoint grouped by active team. For each member return check-in time, Supervisely verification, readiness reason, current job ID/status, and assignment ordinal (first, second, etc.). Count all historical assignments to that person within the plan; show separate completed, correction, and current counts. Return plan status and last worker update.
- In Job Distribution, show teams by name with only checked-in members in the ready list. Show unready teams and reasons separately for organisers, so missing arrivals are visible without treating them as ready. Update on polling and immediately after check-in; avoid one request per team/person.
- Make the launch state explicit: `Ready, waiting for launch`, `Running, waiting for block`, `Publishing`, `Annotating job N`, `Correction needed`, `Supervisely identity missing`.

### 6. Verification and rollout

- Cover solo and two-member teams; missing ID and unverified identity display; check-in without table; table edits; launch gating; concurrent claims; late arrival; checkout; complete/incomplete submission; and per-person job ordinal in targeted local tests.
- Run PostgreSQL migration/rehearsal on a copy of existing event data if schema changes are needed. Confirm that old assignments, review data and team tables remain intact.
- Reconcile `docs/DUAL_DEVELOPER_EVENT_PLAN.md` and `docs/PRODUCT_SPEC.md` with these decisions. Tomas owns frontend and attendance paths in the current division, so coordinate edits there to avoid overwriting concurrent work.

## Event-day sequence

1. Create named teams and verify each participant's Supervisely membership.
2. Check in arrivals with one click; assign tables independently.
3. At the common start time, organiser launches the approved shared queue once.
4. Worker dispatches to checked-in members, then continues handling completed jobs and late arrivals automatically.
5. Staff use Job Distribution to watch each team's members and their current job number/status.

## Open product choices

- If the Supervisely membership service is briefly unavailable at reception, should check-in be blocked until verification succeeds, or should staff be able to record attendance while job readiness stays blocked? This plan uses strict check-in verification as requested.
- Should a team with two members receive two simultaneous 30-image jobs (one per checked-in person)? This plan assumes yes, following the existing per-person queue design.
