# Videos first, standalone images second

An event can use a ZIP/folder-imported video dataset followed by a separate
mounted image dataset. Storage method does not determine scheduling. Both
datasets retain their original asset IDs and Supervisely source mappings.

## Organiser workflow

1. Keep the existing video plan. Standard shared queues require no morphology
   classification. Balanced queues still require their existing eligibility
   checks and must cover every video frame to support this handoff.
2. Link the separate image folder using the dataset page. Its layout is
   `dataset/category/photo.jpg`: images sit directly inside category folders.
   Category names need not identify a species or morphology. Keep the host
   folder available to the API and workers. Finish copying and synchronizing
   before creating its plan.
3. Wait for image indexing and the Supervisely mirror to complete.
4. Under **Job Distribution → Automatic distribution**, select the image
   dataset and **Standalone images · 30-image queue**. Select the existing
   video plan under **Start after video plan**.
5. Create and approve the image draft, then **Arm image plan**. It enters a
   persistent `waiting` state. The video plan can be approved and launched
   before or after arming the image plan; its identity and blocks stay intact.

Image jobs contain 30 images, except the final job, which may be shorter. They
reuse the mirrored image IDs and the existing validation, correction, review,
and scoring paths. Image batches never pretend to be source videos.

## Handoff rules

These are two separate queues, one per dataset and plan. The worker starts
the armed image plan once **every primary video job has been assigned**.
The next eligible annotator can receive an image job in the same worker poll.
Assigned videos can still be in progress, awaiting submission, in correction,
or under review. Their existing validation, correction, review, and scoring
continue independently; neither dataset nor plan is merged or recreated.

Activation also requires an active event and a complete image mirror. A
paused video plan does not trigger activation. The transition is transactional,
audited, and safe to retry after restart or concurrent worker polls. Each
annotator still has at most one open automatic work packet per event.

There is one following image plan per video plan. Unrelated automatic plans
cannot be approved alongside the sequence. A waiting image plan can be
cancelled without deleting either dataset or changing the video plan. After
image jobs begin, use the existing pause controls rather than cancellation.

The migration adds an optional dependency and permits image blocks without
a source-video ID. Existing plans receive no dependency and retain their
current state, source mappings, and assignments.

## Verification and limitations

Tests cover ZIP video imports followed by mounted unclassified images, both
video queue modes, waiting before video approval, complete and partial
submissions, image correction jobs, authorised image previews, monitor review
and scoring, missing mirrors, cancellation, duplicate claims, and concurrent
PostgreSQL activation/allocation. A synthetic 50,447-frame test checks the last
video assignment and next image assignment without submitting the last video.
All remote writes in these tests use the SDK
test double; real event data is not used for mutations.

A read-only production audit can verify remote IDs, names, dimensions, and
local metadata without creating jobs. Allocation can be exercised against an
ephemeral copy of dataset metadata and synthetic participants. Such an audit
does not prove live participant permissions or exercise real remote job writes.

A dataset-preserving rehearsal reset is **not implemented by this change**.
The existing full event reset clears datasets and must not be used as a
substitute for that proposed feature.

## Deployment verification — 2026-11-13

Deployed application commit `2011cb7` with migration `0032_sequential_image_plans`
and all 14 native workers. The public tunnel served the new image-plan controls
and passed the readiness check. Before deployment, all 344 backend tests,
lint, and the frontend build passed.

The Official Event retained its ready ZIP dataset (50,447 frames, 149 videos)
and existing draft plan (1,682 blocks, zero assigned jobs). Ordered checksums
of every asset, source mapping, and block matched before and after deployment;
the dataset and original plan fields also matched exactly. All 50,447 local
media files remained present, with the original media and source mounts intact.
The pre-upgrade database dump was restored successfully into an isolated,
network-disabled PostgreSQL container, where the same checksums matched.
No real jobs were created for deployment verification.
