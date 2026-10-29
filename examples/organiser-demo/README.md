# Two-team organiser demonstration

Use `Start-App.ps1 -Mode Demo -Install` on Windows, or `python -m monitoring.run --demo`, then open **http://127.0.0.1:8000**. This offline version sends no real invitations or API requests. For actual Supervisely upload/invitations/tasks, stop it and start **Pilot** mode instead: `Start-App.ps1 -Mode Pilot` or `python -m monitoring.run --pilot`.

## Requested accounts

| Team | Monitor / Manager responsibility | Annotator |
|---|---|---|
| TEAM 1 | tomas.bf.santos@gmail.com | tomas.gaming.21@gmail.com |
| TEAM 2 | tomas.bf.santos@gmail.com | tomas.santos.work2002@gmail.com |

Each demo team has two people in total: one monitor and one annotator. The production registration flow also supports ordinary event teams with two annotators plus a separate monitor. The organiser account retains native Admin membership when it creates a team; it fulfils the Manager responsibility and is assigned as job reviewer. Native Admin is not downgraded.

The CSV includes `team_name`, `monitor_email` and `member_1_email`; the second annotator columns are blank. Usernames are not a prerequisite for email invitations. The offline simulator creates illustrative IDs/logins; real mode obtains them from native membership records.

## Quick presentation

On a fresh demo, click **Prepare working presentation**, then open **Monitoring**. This shortcut creates both teams, simulates acceptance, uploads both photos to the simulator, freezes example rules and distributes the same two photos independently to each team (replication 2). It releases/submits the first batch for TEAM 1 so judging is immediately available. Other batches remain upcoming. Example annotations are deliberately imperfect, not scientific ground truth.

If an older saved roster is loaded, click **Reset this demo** first. Previous records are backed up under `data/organiser-demo/backups/`. Reset is never available for live native resources.

## Manual walkthrough

1. **Monitors & Teams → Load bundled demo CSV → Create teams and invite CSV emails.** Both native-shaped teams appear immediately with **Pending invitations**. Their annotators are not yet ready for jobs; the organiser's existing Admin membership is retained.
2. Inspect each member's status. Click **Refresh all invitations**: refreshing does not resend emails or create duplicate teams.
3. In the offline demo, click **Simulate invitation acceptance** for each team. In Pilot mode, each actual annotator accepts in Supervisely, then the organiser clicks **Refresh all invitations**. Each team becomes **Ready** only after required memberships/roles are confirmed.
4. Open **Dataset**. Click **Load bundled CC0 bird photos**, or use **Upload dataset → Or select a complete dataset folder** and select this demo's `dataset/` folder. The README in the empty video branch is ignored. Both same-named image files retain their different relative paths.
5. Select the photo source datasets and both ready teams. Use replication **1**, image effort **1** and batch effort **1** to divide the two photos evenly: **one unique photo per team**, zero workload spread. Use replication **2** instead if both teams should independently annotate both photos for comparison. Counts are illustrative inputs, never event-size assumptions.
6. Choose the scoring policy/guide and **Save and freeze official rules**. Preview the distribution before applying it. Each team receives its own copied entities and native task ownership mapping.
7. In **Monitoring**, select TEAM 1 and **Release this batch**. Repeat for TEAM 2. In real mode, each corresponding annotator opens its native Supervisely job, annotates and submits. In offline mode, **Simulate participant submission** supplies illustrative labels.
8. Refresh/capture the submission. Load its image, judge quality and inspect standings. Try a correction/resubmission and a fully reviewed native acceptance. Pending review is distinct from a zero score.
9. Close the demonstration and export records, including memberships, source mappings, annotations, rules and judgments. Reset the offline demo to rehearse again; no real native resources are deleted automatically.

No video media is included. Production allocation keeps every video whole; there is no automatic clip/frame-range splitting. Online presence is intentionally omitted.

## Photo provenance

- `dataset/images/sparrows/bird.jpg`: house sparrow by Mathias Appel, CC0; https://commons.wikimedia.org/wiki/File:House_Sparrow(Passer_domesticus).jpg
- `dataset/images/garden/bird.jpg`: house sparrow by Hobbyfotowiki, CC0; https://commons.wikimedia.org/wiki/File:House_sparrow_(Passer_domesticus),_Amrum.jpg

Both were resized to 960 pixels wide and JPEG recompressed. `image-credits.json` contains source/download/license URLs and file SHA-256 checksums. License: https://creativecommons.org/publicdomain/zero/1.0/ . They are software demonstration inputs, not a representative scientific sample.

See [real pilot](../../docs/real-pilot.md) and [email invitation integration](../../docs/email-invitations.md) for live checks. Offline success does not verify actual invitation delivery, permissions or licence/seat limits.
