# Production deployment and DEMO delivery

Production and DEMO use the same domain services and three-tab interface. DEMO and Pilot both use the real production gateway with the supplied sample accounts/photos. They share their saved local setup and connect the organiser token in the app. Production uses native multi-user authentication and separate PostgreSQL storage. The explicitly named Simulator is an offline development tool, isolated from these live paths.

## Deliverables

| Version | How to start | Intended use |
|---|---|---|
| DEMO | `Start-App.ps1 -Mode Demo -Install` or `python -m monitoring.run --demo` | Connect the organiser token; create real teams/invitations, upload the sample photos and release real native tasks. Participants annotate/submit in Supervisely. |
| Real-account rehearsal | `Start-App.ps1 -Mode Pilot -Install` or `python -m monitoring.run --pilot` | Alias for the same live DEMO workflow, database and saved connection. Switching modes does not provision twice. |
| Offline Simulator | `Start-App.ps1 -Mode Simulator` or `python -m monitoring.run --simulator` | Development only; no email delivery or native resources. |
| Production | Native Supervisely session running `python -m monitoring.run --production` | Several authenticated monitors, one shared event, persistent PostgreSQL. No simulator controls or saved local token. |

GitHub Actions runs only through **Run workflow**. Pulling, installing, building, testing and running these versions do not require an Actions subscription or quota.

## Deploy the production app

The runtime is a **native Supervisely application session**, using its authenticated multi-user gateway. A plain publicly exposed Uvicorn container is not an alternative authentication system. The production entry point refuses local/demo modes and requires PostgreSQL. One application process serves concurrent users; use one shared hosted session for the event. The supplied Dockerfile is a build recipe; no container image has been published by this repository.

1. Run the [real pilot](real-pilot.md), including native membership, invitations, task ownership, correction/acceptance, reviewer handover and two simultaneous authenticated monitors. Record the actual instance version and resource/seat limits. The three supplied email addresses alone are insufficient to execute these checks from a development environment.
2. Build the image locally, then publish it to a registry accessible by the Supervisely agent. Replace `YOUR_REGISTRY` and `YOUR_TAG` below with your own registry/tag. Docker and registry access are deployment infrastructure, not event configuration.

   ```bash
   docker build -t YOUR_REGISTRY/supervisely-monitoring:YOUR_TAG .
   docker push YOUR_REGISTRY/supervisely-monitoring:YOUR_TAG
   ```

3. Prepare a separate native release source folder, using the same image tag (prefer its immutable digest) and the instance version actually verified in the pilot:

   ```bash
   python -m monitoring.release --image YOUR_REGISTRY/supervisely-monitoring:YOUR_TAG --instance-version X.Y.Z
   ```

   Replace `X.Y.Z` with the numeric actual instance version. The command refuses an untagged image, invalid version or existing output directory. `dist/supervisely-release/` contains application sources, static assets, the sample inputs, native `config.json`, documentation and SHA-256 checksums. It contains no `.env`, runtime database, credentials, test workflows, Python cache or `requirements.txt`. Dependencies are already installed in the image. Use this generated folder as the root of the private Supervisely app release repository. The development repository's root configuration is not a substitute for this instance-specific release.

4. Connect the agent to persistent PostgreSQL. `compose.yaml` supplies a database for a controlled host if suitable for your infrastructure; its container hostname `database` resolves only on that Compose network. From a separately hosted Supervisely agent, use the database's actual reachable private hostname and TLS policy. Configure `DATABASE_URL=postgresql+psycopg://...` in the deployment environment. Percent-encode special characters in URL credentials. Do not publish the database to the Internet.
5. Mount persistent writable upload staging and set `UPLOAD_STAGING_DIR` to that path. Event records, captures, scores and audit live in PostgreSQL. Staged media must also survive any restart during setup. Reserve space for inventory/captures based on pilot measurements. The Docker image runs as UID 10001; provision writable volume permissions accordingly, or follow your agent's container-user policy.
6. Launch the private app from the monitoring team's Supervisely UI as a native Admin. The platform supplies session context and per-user credentials. Configure groups, registration import, assignments, dataset and rules in the app; there are no event IDs/roster settings in environment files. Hosted tokens are not saved to the database.
7. If unattended polling/deadline closure is required, supply `SYNC_API_TOKEN` as an infrastructure secret with verified access to all participating teams. Without it, monitor sessions refresh only their assignments, and the organiser can close explicitly. The credential must allow job stopping for automatic closure, not merely reading progress. Do not commit it or place it in app state/exports.

Supervisely's release guidance requires a prebuilt image and compatible `instance_version`, and disallows `requirements.txt` beside a release configuration. The packaging command implements that source layout. References: [app configuration](https://developer.supervisely.com/app-development/basics/app-json-config/config-json), [release compatibility](https://developer.supervisely.com/app-development/basics/compatibility).

## Operate and update

- `/healthz` returns only status and mode; unavailable storage produces HTTP 503 without connection details. The container health probe checks it every 30 seconds. Health success establishes process/database availability, not native API permissions or scientific correctness.
- Back up PostgreSQL before each update and on the event schedule. Verify a restore into a separate database before the event. Archive the image digest, native release checksum file and the rules/export used for results. The local pilot's credential key must accompany its database if that rehearsal is backed up.
- Keep one event per database. Test an upgrade against a restored copy first. Initialization adds missing tables and preserves existing records; incompatible future schema changes require an explicit migration. Stop the application during deployment/schema updates rather than mixing different source revisions on the same database.
- Inspect recorded resource IDs when an operation is uncertain. Acknowledging a failed operation does not repair its native changes. There is no destructive automatic cleanup of participant work.
- Review `synced_at`/stale warnings, failed polling logs, operation-history blocks, database/staging capacity and actual monitor workload during the event. Rehearse database loss/reconnection and an interrupted native write.
- Close the event through the organiser control, finish review and export complete records. Closure/captures record observed state, not an atomic platform-wide state at an exact deadline instant. Agree the competition's interpretation before starting.

## What is verified here

Local Python and browser tests cover domain flows, token/assignment checks, optimistic concurrency, native-shaped jobs, captured-photo rendering, human scoring, recovery guards and release packaging. The SDK is pinned. The container recipe cannot establish instance-specific invitations/permissions or a production capacity guarantee. Those require the real pilot and shared hosted rehearsal. The production implementation is delivered with these explicit deployment acceptance checks; successful simulator output does not certify a live event deployment.
