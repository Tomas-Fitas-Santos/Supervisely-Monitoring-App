# Supervisely Monitoring App

A shared organiser/monitor app for a bird-annotation event. Participants work in **native Supervisely**, using their own existing accounts. The app manages registrations, independent pair annotations, task release, submission captures, human judging and competition standings.

**There are no fixed event totals.** Teams, source inventory, replication, frame requirements and batch effort are inputs. The included two teams (one monitor plus one annotator each) and two photos are test inputs, not estimates of event size. Ordinary event teams can still have two annotators.

The same application has two delivery modes: a **production app** for authenticated Supervisely users and a **DEMO** for organiser presentations. A local real-account pilot runs the production integration against the supplied test accounts before hosted deployment. GitHub Actions is optional and manual-only; neither installation nor execution uses Actions.

| Start | Purpose | Supervisely operations | Storage |
|---|---|---|---|
| `python -m monitoring.run --demo` | Working organiser presentation, no token required | Simulated, with the supplied emails displayed as roster data | `data/organiser-demo/` |
| `python -m monitoring.run --pilot` | Real-account rehearsal with the supplied two teams | Actual API/SDK operations after connecting in the app | `data/account-pilot/` |
| `python -m monitoring.run --production` | Shared production session inside Supervisely | Actual per-user authenticated operations | Persistent PostgreSQL |

On Windows, `Start-App.ps1 -Mode Demo` and `Start-App.ps1 -Mode Pilot` handle the local environment and Windows libmagic dependency. Add `-Install` after pulling dependency changes. The default mode is Demo. [Production deployment](docs/production.md) includes the image, native release packaging, persistence and operational checks.

| Main tab | Workflow |
|---|---|
| **Monitors & Teams** | Import Google Forms CSV responses, create native teams, invite by email, see individual Pending/Accepted states and assign monitors. Click a monitor to inspect or transfer assignments. |
| **Dataset** | Upload a complete folder tree or select existing nested datasets. Set event rules, inspect source IDs, configure replication/effort, preview source coverage and distribute independent annotation copies. |
| **Monitoring** | Select a team; inspect current, submitted and upcoming batches; see member-level native tasks; capture submissions; browse images/required frames; judge quality; return corrections; complete accepted native tasks; export records and standings. |

## Run the organiser demonstration

The simulator uses real **CC0 bird photographs**, the supplied real email addresses as roster data, simulated native IDs/logins and deliberately imperfect example labels. It creates no actual Supervisely accounts, teams or jobs. It runs on loopback in a separate persistent SQLite database. No API token or `.env` is needed. All frontend assets for local mode are bundled.

Windows PowerShell, Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --force-reinstall --no-deps -r requirements-windows.txt
.\.venv\Scripts\python.exe -m monitoring.run --demo
```

If the environment already exists, use just the last command. Open **http://127.0.0.1:8000**. Follow [the organiser demo walkthrough](examples/organiser-demo/README.md).

Click **Prepare working presentation** to create TEAM 1 and TEAM 2, simulate invitation acceptance, distribute both photos to each team, freeze example rules and submit the first simulated batch. Open **Monitoring** to show image inspection, judging, standings and upcoming batches. The presentation shortcut uses replication 2 so both teams receive identical material. For the manual walkthrough, replication 1 divides the two photos evenly: one unique photo per team. If an older demo is loaded, use **Reset this demo** first; it preserves a backup.

Linux/macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m monitoring.run --demo
```

The Windows extra installation resolves `python-magic`'s missing native `libmagic` loader. Repeat it after reinstalling the main requirements on Windows.

Demo files are in [examples/organiser-demo](examples/organiser-demo/): registration CSV, two small photos in nested folders with the same basename, an empty video branch, and provenance/license records. The two photos deliberately exercise path preservation. Example annotations are **not ground truth**.

The demo resumes its previous state on restart. **Reset this demo** backs up its results under `data/organiser-demo/backups/` before clearing the simulator. This action is unavailable in live mode.

## Test the supplied real accounts

```powershell
.\.venv\Scripts\python.exe -m monitoring.run --pilot
```

Open **http://127.0.0.1:8000** and connect using the API token belonging to **tomas.bf.santos@gmail.com** on the same Supervisely instance as the participants. Create/adopt the two event groups. Click **Load bundled demo CSV**, then **Create teams and invite CSV emails**. The requested roster is:

| Team | Monitor / Manager responsibility | Annotator |
|---|---|---|
| TEAM 1 | tomas.bf.santos@gmail.com | tomas.gaming.21@gmail.com |
| TEAM 2 | tomas.bf.santos@gmail.com | tomas.santos.work2002@gmail.com |

Teams appear immediately with per-member invitation status. Sign in as each annotator and accept the native invitation. Click **Refresh all invitations**; both teams must say **Ready**. Your organiser retains native **Admin** membership when creating these teams and performs the Manager/reviewer responsibility; the app never demotes the team's required Admin. In Dataset, click **Load bundled CC0 bird photos** or upload your folder, freeze rules, then preview/apply distribution with replication **1** to give one photo to each team. Release each team's job from Monitoring. Each annotator works and submits in Supervisely; return here to capture and judge.

No native account IDs or logins are invented in this mode, and global account search is not a prerequisite. Native IDs are obtained from invitation responses and confirmed accepted team memberships. Refreshes do not resend pending invitations. Definite native rejections remain visible without blocking other setup; uncertain writes retain checkpoints for inspection. See [invitation API details and live-validation limits](docs/email-invitations.md) and the [real pilot](docs/real-pilot.md). This mode ignores legacy `.env` event IDs/tokens and uses its own encrypted connection and database.

## Run a real event

```powershell
.\.venv\Scripts\python.exe -m monitoring.run
```

A fresh local install initializes its database automatically. Connect the organiser's Supervisely server and API token **inside the app**. Initialize the Monitors/Annotators groups, then use the three tabs. The connection is encrypted at rest; its local database and credential key belong together. Event people, IDs, assignments and rules do not belong in environment files.

See [the real pilot guide](docs/real-pilot.md) for the required rehearsal and shared deployment.

For simultaneous real monitors, deploy **one native multi-user Supervisely app session** with persistent PostgreSQL. Each request uses the current user's platform API credential, verifies the credential owner, and enforces app assignments. Organiser oversight/adjudication requires current monitoring-team Admin permission. Annotators are denied app access. Local mode represents the connected organiser; the demo role switch is unavailable for real events.

Without a hosted background polling credential, signed-in monitors refresh their own teams through a database lease once per interval; multiple tabs do not start separate polling loops. An optional deployment polling credential can refresh all participating teams and observe deadlines when no monitor browser is open. Hosted database access and optional server credentials are deployment infrastructure, separate from in-app event configuration.

## Registration and allocation

- CSV accepts one team per row (one or two annotators, optional monitor_email), or two participant rows grouped by team name. Map the form columns. Monitors are separate from annotators and can supervise multiple teams; duplicate annotator emails and conflicting reimports are rejected.
- Email onboarding creates the native team before waiting for invitations. Pending members, definitive invitation errors, accepted memberships and readiness are separate states. Raw native membership checks retain invitation information which the SDK UserInfo tuple omits. Accepted native IDs and correct roles are required before distribution/release. The Add team form uses the same email-invitation flow; legacy username-based backend operations remain available for existing integrations.
- A pair currently has its own native Supervisely team and independent destination entities. The organiser retains administrative ownership. Verify team/seat quotas on the actual instance before provisioning the full roster.
- Allocation is seeded, greedy effort balancing, with source-stratum tie balancing, configurable replication and an optional common scored challenge. Image effort and batch effort are estimates supplied/reviewed by the organiser; they are not measured annotation speed. Videos stay whole; multiple videos per pair are supported and alternate between the two members. Native video ownership can be handed over explicitly.
- Previews show actual counts, effort spread, source coverage and required image/frame units. SDK copying is partitioned by source dataset, so actual batch counts can exceed the planner's initial packing groups. Source paths and original-to-copy IDs are retained.
- Uploaded media is chunked through the authenticated request path. Folder upload handles the image/video branches together, preserves nested paths and rejects ambiguous paths. It accepts raw media, not arbitrary dataset ZIP formats. Existing Supervisely datasets are listed recursively.

## Competition rules and review

In **Dataset → Event rules**, choose whole-image/frame, per-criterion or weighted scoring; configure good/bad/missing values, enable or disable missing penalties, choose scored criteria, set required video frame stride and define the annotation/occlusion/empty-frame guide. Choose a common challenge if rankings should compare identical material. Save and freeze the policy before judging.

Good/bad/missing/not applicable/pending are distinct. Pending work does not become a zero judgment. Missing penalties require an explicit human judgment on a captured required unit. The system does not infer penalties for unreleased work, or infer anatomical correctness from figure counts. Per-bird decisions are keyed to a submission, image/frame, bird identity and criterion, preventing repeated points for duplicate clicks. Changed captures do not reuse earlier judgments. Ties retain the same ranking position; standings remain provisional until closure and complete review.

Native submission, structural presence/completeness, monitor judgment and native acceptance are separate states. The gallery has paginated filters and frozen-payload previews; live inspection/operational flags are collapsed separately. Review shortcuts are listed in the judging panel.

Next-batch release can follow submission or explicit approval. Correction tasks stop the affected member's superseded job and reissue that member's complete task with an image/frame-specific report, preserving its denominator and existing annotations. Native acceptance requires every required unit/criterion judged good or not applicable and a check that the live annotations still match the judged capture. Unknown remote outcomes block further setup/release until inspection.

## Captures, deadlines and recovery

Captures store annotation JSON, metadata, a digest and the time the app observed the state. They are not guaranteed exact native submission-time snapshots. A deadline blocks new releases. Automatic closure requires a working organiser/service polling credential; otherwise the organiser closes the event explicitly. Closure stops current native tasks before capturing final observed annotations. It is not an atomic platform-wide, exact-deadline transaction. Review can continue on retained captures after closure.

Exports include source mappings, captured annotations, rule version, assessments, correction lineage, closure and audit history. They do not contain API tokens or collect uncaptured work automatically. Keep real registration/export files private. Back up PostgreSQL and local encryption keys before event updates.

This update adds tables through the existing initialization path without dropping previous tables or altering existing review columns. Legacy operational reviews remain intact and do not silently become competition scores without captured annotation evidence. Setup failures preserve resource checkpoints. Inspect **Operation history and recovery** before acknowledging a partial write; acknowledgment does not repair it or replay it. Automatic crash reconciliation of arbitrary partial native provisioning is not implemented.

## Verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm install --prefix /tmp/monitor-ui --no-audit --no-fund vue@2.7.16 jsdom@26.1.0 playwright@1.56.1
NODE_PATH=/tmp/monitor-ui/node_modules node tests/ui_smoke.cjs
/tmp/monitor-ui/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/monitor-ui/node_modules python tests/browser_demo.py
```

Node/Chromium are development-only checks; running the app needs no Node build. GitHub Actions checks are **manual-only** (`workflow_dispatch`), so pushes and pull requests do not consume Actions minutes. Run checks locally with the commands above. Set `TEST_DATABASE_URL` only to a **disposable** database: the Python tests drop its schema.

Live instance permissions, invitation behavior, native correction/acceptance transitions, simultaneous platform sessions, API throughput and actual event-scale review capacity still require the real pilot. No live Supervisely credentials were provided in this development environment. See [current capability status](docs/implementation-roadmap.md).
