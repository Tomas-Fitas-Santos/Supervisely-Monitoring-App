# Checked-out video claims returned on 14 November 2026

The organiser authorised returning the 30 previously identified unfinished video
jobs from 15 fully checked-out teams, while keeping the application online.
The operation was limited to those exact job numbers in the Official Event.

## Result

- 30 old Supervisely attempts stopped and archived, with no remote jobs, images,
  datasets, or annotations deleted.
- 756 unresolved frames returned as 26 ordinary shared-queue blocks (25 × 30,
  plus a final block of 6).
- 144 structurally complete frames retained for monitor review under their
  original teams: 99 previously validated frames and 45 newly recovered from
  saved, unsubmitted annotations. Structural completeness does not award a
  correctness score; monitors still grade them normally.
- Original attempts were finalised with only their retained frame subset and
  `review_status=requeued_checkout`; their original full source IDs and owners
  remain in the return journal. Their work packets were closed. This prevents
  automatic validation from reopening frames now owned by the shared queue.
- Every one of the video dataset's 50,447 source frames occurs exactly once in
  the queue's retained/assigned/unassigned coverage after the transaction.
- Fourteen reclaimed jobs were confirmed published in Supervisely at final
  verification, with matching assignees, datasets and image counts. Queue
  consumption continues normally as checked-in annotators become available.

## Preservation and validation

The maintenance utility is [checkout_requeue.py](../src/annotatiathon/checkout_requeue.py).
It requires an explicit event and job-number allowlist; its default mode performs
eligibility checks and rolls back without contacting the remote write API.
Durable `NativeOperation(kind=checkout_requeue)` records and audit events retain
each old owner, native job ID, source frame partition and backup location.

The native job is stopped before annotation capture, then archived only after
complete-frame validation and a separate backup of all unresolved annotation JSON.
The final queue repack uses the existing plan lock and checks exact source-set
equality before committing. Ordinary workers require no new application code.

The publishing check caught the global `uq_native_media_image_id` constraint:
obsolete `NativeMediaInstance` rows still owned the returned frames. Releasing
only those 756 obsolete references, while retaining the canonical mirror receipts
and accepted-frame references, allowed the existing publisher to reuse the images.
Eleven affected publish operations were safely retried after confirming they had
failed before native dataset/job publication; deterministic publication also
reconciles any existing remote identity. All affected operations then succeeded.
The utility and regression test now include this reference release.

Four maintenance regression tests cover partial-work retention, exact unique
coverage, actual publisher image-reference reuse, idempotency, check-in races,
submission races, unconfirmed remote stops, and prevention of stale validation.
Six existing sampled-review tests also passed. The optional PostgreSQL concurrency
test was skipped because its disposable test URL was not configured. Live database
and remote checks verified the actual resulting queue and published jobs.

## Recovery receipts

- Before-operation online database/configuration backup:
  `backups/recovery/20261114-023323-6b75d9e3/`.
- Final verified online database/configuration backup:
  `backups/recovery/20261114-025021-cce0f8fe/`.
- Local annotation/journal archive:
  `backups/recovery/nightjar-checkout-requeue-recovery-20261114.zip`;
  210 JSON files plus the return journal, 203,261 bytes.
- Archive SHA-256:
  `5B2ABD5195EC61DCDA2C5AC2948D439147C196AA63437CF6CE9645C8587A89F8`.

Public monitoring recorded **486 successful health checks and zero failures**.
The existing API, web container, database, tunnel and workers stayed running;
no deployment, migration or service restart was performed. The utility was
executed separately inside the existing SDK worker container.

This was an explicitly authorised batch return, not a change to future checkout
policy. Future checkouts still do not automatically cancel or return work.
