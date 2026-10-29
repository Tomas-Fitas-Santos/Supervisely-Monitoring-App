# Two-team organiser demonstration

Use `Start-App.ps1 -Mode Demo -Install` on Windows, or `python -m monitoring.run --demo`, then open **http://127.0.0.1:8000**. Demo and Pilot are the **same live Supervisely workflow**, using the same saved database and connection. Connect the organiser token in the app. Teams, email invitation requests, uploads and labeling jobs are real. Only the roster and dataset are small sample inputs.

## Requested accounts

| Team | Monitor / Manager responsibility | Annotator |
|---|---|---|
| TEAM 1 | tomas.bf.santos@gmail.com | tomas.gaming.21@gmail.com |
| TEAM 2 | tomas.bf.santos@gmail.com | tomas.santos.work2002@gmail.com |

Each demo team has two people in total: one monitor and one annotator. The production registration flow also supports ordinary event teams with two annotators plus a separate monitor. The organiser account retains native Admin membership when it creates a team; it fulfils the Manager responsibility and is assigned as job reviewer. Native Admin is not downgraded.

The CSV includes `team_name`, `monitor_email` and `member_1_email`; the second annotator columns are blank. Usernames are not a prerequisite for email invitations. The offline simulator creates illustrative IDs/logins; real mode obtains them from native membership records.

## Updating from the previous offline Demo

Stop the server, pull the implementation branch and restart with `-Mode Demo`. The old offline records stay untouched in `data/organiser-demo/`. Live Demo and Pilot both use `data/account-pilot/`, so an existing real pilot is resumed rather than provisioned again. Simulated pending invitations never sent an email and must not be treated as native teams.

## Quick live setup

In **Monitors & Teams**, the **Set up the demo event** panel and manual **Initialize event** control stay visible before connection. Connect the organiser token above, then click **Set up sample event** to initialize groups, load the CSV, create the native teams/request invitations and upload the sample photos. The organiser appears immediately as a monitor. Inspect pending/errors below, have annotators accept in Supervisely, then configure distribution and release jobs. The shortcut does not accept invitations, freeze rules or fabricate submissions. Confirmed teams/uploads are reused on a repeat; unresolved writes still require inspection.

## Manual walkthrough

1. Connect the API token belonging to **tomas.bf.santos@gmail.com** and create/adopt the event groups. Then **Monitors & Teams → Load bundled demo CSV → Create teams and invite CSV emails.** Both real native teams appear immediately with **Pending invitations**. Their annotators are not yet ready for jobs; the organiser's existing Admin membership is retained.
2. Inspect each member's status. Click **Refresh all invitations**: refreshing does not resend emails or create duplicate teams.
3. Each actual annotator accepts the invitation in Supervisely, then the organiser clicks **Refresh all invitations**. If the app reports an invitation API rejection, use **Open native invitations**, select the named team and invite the same email there before refreshing. Each team becomes **Ready** only after required memberships/roles are confirmed.
4. Open **Dataset**. Click **Load bundled CC0 bird photos**, or use **Upload dataset → Or select a complete dataset folder** and select this demo's `dataset/` folder. The README in the empty video branch is ignored. Both same-named image files retain their different relative paths.
5. Select the photo source datasets and both ready teams. Use replication **1**, image effort **1** and batch effort **1** to divide the two photos evenly: **one unique photo per team**, zero workload spread. Use replication **2** instead if both teams should independently annotate both photos for comparison. Counts are illustrative inputs, never event-size assumptions.
6. Choose the scoring policy/guide and **Save and freeze official rules**. Preview the distribution before applying it. Each team receives its own copied entities and native task ownership mapping.
7. In **Monitoring**, select TEAM 1 and **Release this batch**. Repeat for TEAM 2. Each corresponding annotator opens its native Supervisely labeling job, annotates and submits. The live demo does not supply example labels or simulate submission.
8. Refresh/capture the submission. Load its image, judge quality and inspect standings. Try a correction/resubmission and a fully reviewed native acceptance. Pending review is distinct from a zero score.
9. Close the demonstration and export records, including memberships, source mappings, annotations, rules and judgments. No real native resources are reset/deleted automatically.

No video media is included. Production allocation keeps every video whole; there is no automatic clip/frame-range splitting. Online presence is intentionally omitted.

## Offline development simulator (optional)

`Start-App.ps1 -Mode Simulator` or `python -m monitoring.run --simulator` explicitly starts the former offline walkthrough. Only this mode has **Prepare working presentation**, simulated acceptance/submission and a backed-up reset. It creates no real teams/jobs and sends no invitations. Its records stay separate from the live demo. It is not the organiser's real-account demonstration.

## Photo provenance

- `dataset/images/sparrows/bird.jpg`: house sparrow by Mathias Appel, CC0; https://commons.wikimedia.org/wiki/File:House_Sparrow(Passer_domesticus).jpg
- `dataset/images/garden/bird.jpg`: house sparrow by Hobbyfotowiki, CC0; https://commons.wikimedia.org/wiki/File:House_sparrow_(Passer_domesticus),_Amrum.jpg

Both were resized to 960 pixels wide and JPEG recompressed. `image-credits.json` contains source/download/license URLs and file SHA-256 checksums. License: https://creativecommons.org/publicdomain/zero/1.0/ . They are software demonstration inputs, not a representative scientific sample.

See [real pilot](../../docs/real-pilot.md) and [email invitation integration](../../docs/email-invitations.md) for live checks. Offline success does not verify actual invitation delivery, permissions or licence/seat limits.
