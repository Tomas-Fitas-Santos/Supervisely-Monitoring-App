# Organiser demonstration

Run `python -m monitoring.run --demo` from the repository root using the project's Python environment, then open **http://127.0.0.1:8000**. On Windows use `.\.venv\Scripts\python.exe -m monitoring.run --demo`.

This is an interactive, persistent simulation of Supervisely operations. The photos and supplied roster emails are real; native IDs/logins, tasks, submissions and illustrative labels are simulated. No actual accounts or native resources are created. Participants in a real event use Supervisely, never this app. For an actual-account demonstration, use `--pilot` and the [real pilot guide](../../docs/real-pilot.md).

For a presentation, click **Prepare working presentation** on a fresh demo, then open **Monitoring**. It runs the same registration, provisioning, upload, distribution, release and capture services against the simulator. One batch is submitted and ready for judging; the other is upcoming. Use the manual walkthrough below if you want to demonstrate setup. Reset with a backup first if an older demo roster is already loaded.

## Included inputs

- `registrations.csv`: **Pilot Bird Team**, with `tomas.gaming.21@gmail.com` and `tomas.santos.work2002@gmail.com`. Native login columns are blank intentionally; resolve accounts on the actual instance in real mode.
- `accounts.json`: supplied organiser email `tomas.bf.santos@gmail.com` and the two participant emails. These are labels/registration data, not credentials or authorization.
- `dataset/images/sparrows/bird.jpg`: house sparrow photo by Mathias Appel, CC0.
- `dataset/images/garden/bird.jpg`: house sparrow photo by Hobbyfotowiki, CC0.
- `dataset/videos/`: empty media branch, with a README. This tiny bundled rehearsal uses images only.
- `image-credits.json`: original file pages, authors, download URLs, license URLs, resize/recompression information and SHA-256 checksums.

Original photo pages:

- https://commons.wikimedia.org/wiki/File:House_Sparrow(Passer_domesticus).jpg
- https://commons.wikimedia.org/wiki/File:House_sparrow_(Passer_domesticus),_Amrum.jpg

Both photographs were resized to 960 pixels wide and JPEG recompressed. The files share a basename intentionally: their different source folders must survive import. Photo terms: https://creativecommons.org/publicdomain/zero/1.0/ . These are software demonstration inputs, not a representative scientific sample or ground-truth pose labels. The fixture counts are not event-size assumptions.

## Walkthrough

1. In **Monitors & Teams**, click **Load bundled demo CSV**, then **Match all registration emails**. Expand each pair's information. Select Demo organiser or Demo monitor and click **Provision this pair** for each team. Alternatively, leave a pair unassigned and assign it from the monitor directory afterwards.
2. Click a monitor to see their teams. Try assigning/transferring a team or removing a pre-release assignment. The full table shows the two participants and monitor/unassigned status.
3. In **Dataset**, click **Load bundled CC0 bird photos**. The source table shows the nested datasets and actual counts. Parent datasets with zero direct files may also appear; selecting the two photo datasets is sufficient.
4. Open **Event rules and scoring**. For the first rehearsal keep whole-image scoring. The demo uses replication 1, image effort 1 and batch effort 1 so the pair gets both photos in separate batches. These values are illustrative. Choose the desired scoring policy/guide, then **Save and freeze official rules**.
5. Select both photo source datasets and the registered team in **Distribute dataset to teams**. Enter/check replication 1, image effort 1, batch effort 1. Click **Preview distribution**, inspect coverage and counts, then **Apply distribution**. Image ownership alternates across dataset/batch boundaries, so both participants receive work.
6. Open **Monitoring** as Demo organiser, select one of that monitor's teams and release its first upcoming batch.
7. In **Submission gallery and competition judging**, click **Simulate participant submission**. This places deliberately imperfect example labels on the photo and submits the simulated native task. It does not provide scientifically correct reference labels.
8. Select the image tile and click **Load this captured image / frame**. Judge it good, bad, missing or not applicable; provide reasons where required. Observe the competition standings. Unreviewed criteria remain pending. Repeat judgments update a record rather than adding points repeatedly.
9. To demonstrate corrections, record a bad/missing criterion with a specific reason and click **Return corrections as native tasks**. The simulated correction job keeps the existing annotations and contains the correction report. Simulate its submission and inspect the new version; earlier judgments remain in history.
10. For a fully good/not-applicable batch, enter a batch inspection summary and use **Accept fully reviewed batch and complete native tasks**. If labels changed after review, acceptance is rejected until a new capture is judged. Depending on the release rule, submission or approval unlocks the next batch.
11. Switch **Demo role** to Demo monitor. That view has its own assigned teams and cannot provision or change event rules. Return to the organiser and enable oversight to inspect all teams and adjudicate judgments.
12. Use **Close event** to stop current tasks and retain observed final annotations. Finish judging and download the event JSON. Future unreleased work is excluded. Scores remain provisional where review coverage is incomplete.
13. Click **Reset this demo** to rehearse another policy. Previous fictional records and simulator resources are backed up locally before reset. Restarting without reset resumes the current demo.

The real Chromium test automates the main path from CSV import through scoring, role isolation, closure and export, and checks that the browser makes no external network requests.

## Move to a real pilot

Stop the demo, run `python -m monitoring.run --pilot`, and connect the API token belonging to `tomas.bf.santos@gmail.com`. The same bundled CSV and photo buttons become real provisioning/upload actions. Use the [real pilot checklist](../../docs/real-pilot.md). The offline simulator's IDs/logins are never reused as actual native identities.
