# Competition roster, balanced monitor allocation, and event check-in

Implementation branch: feat/roster-checkin-20261105. These capabilities extend the
existing monitoring application, using the same application in DEMO and PRODUCTION.

## User interface

The app now has **four main tabs**:

1. **Monitors & Teams** — import the registration roster, list competition teams
   and their members, manually assign monitors, or preview/apply balanced allocation.
2. **Dataset** — the existing dataset upload and annotation assignment interface.
3. **Participant Check-in** — search by team, person or email, see arrival status,
   check in each participant and undo an entry with an audit reason.
4. **Monitoring** — the existing annotation monitoring and review interface.

An arrival check-in is not Supervisely invitation acceptance, a job submission
or approval. Physical arrivals are tracked independently.

## DEMO and PRODUCTION

DEMO uses an isolated sample database and a separate sample Excel registration
workbook. PRODUCTION uses the official registration workbook and a persistent
production database. Both use the same importer, roster, check-in service
and four-tab interface.

Registrations accept CSV or Excel (.xlsx). The Excel reader allows selection
of worksheet and header row, converts its values to UTF-8 CSV without changing
the original workbook and reuses the validated column-mapping workflow.
Map the Portuguese registration columns directly in the interface.

Competition pairs have stable application registration IDs independent of
Supervisely native team IDs. Balanced allocation requires **two distinct
members** per competition pair, even for DEMO.

## Balanced monitor allocation

Select staff monitors and optionally rebalance existing not-yet-provisioned
pairs. The preview uses a deterministic least-loaded assignment, breaking
ties by monitor ID. It displays resulting team counts and per-team changes.
Provisioned teams stay assigned to the previous monitor.

Apply revalidates the roster fingerprint and monitor membership, resolves
the selected monitors' email identities from their native profiles and commits
the app-level assignments atomically with one audit entry per change.
Unprovisioned pairs can also be manually assigned.

Already provisioned native teams require the existing reviewer-handover
workflow to change monitoring permissions; a roster-only update never
silently changes live jobs.

## Attendance / check-in rules

The organiser sees every pair; a monitor sees only their assigned pairs.
DEMO dual-role allowances are scoped to the demo and do not grant native
Supervisely staff rights by themselves.

Attendance records are keyed by registration ID and member email.
Each event records actor ID, time and reason if reversed. Repeat check-ins
are idempotent. A pair is fully checked in only when exactly two members
are registered and both have arrived. Legacy one-member teams are
flagged Incomplete registration and cannot appear fully arrived.

Attendance persists across valid registration reimports. A missing native
invitation or pending annotation job does not block physical check-in.

## Remaining end-to-end gates

**This branch implements roster distribution and attendance, not the entire
single-native-team migration.** The older onboarding still creates a new
native Supervisely team per competition pair. The prior demo sample still
contains two one-annotator teams, not the agreed single two-person pair.

Before treating the full event system as complete, migrate native provisioning
to one selected Supervisely team, update DEMO to one pair with two real
annotators and native staff permissions, prove cooperative editing of a
shared batch and complete video, verify single submission/single pair score,
and validate actual hosted roles, quotas, storage and deadlines.

These tests are newly added but have not yet been executed against a live
Supervisely instance. Do not commit personal registration data or API tokens.

## Verification (PowerShell)

~~~powershell
git fetch origin
git switch feat/roster-checkin-20261105
git pull --ff-only origin feat/roster-checkin-20261105
py -3.12 -m pip install -r requirements.txt
py -3.12 -m pip install -r requirements-dev.txt
py -3.12 -m pytest -q tests/test_attendance.py tests/test_excel_import.py
~~~

Run the complete pytest and Vue browser suite and a real authenticated
multi-user demo before merge.
