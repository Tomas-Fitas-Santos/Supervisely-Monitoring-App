# Sampled review and scoring handoff

Backend commits on main: `4dbc652` (shared 30-frame queue) and `4993960` (worker publishing lane, sampled review and scoring).

## Start the updated backend

1. Run `git pull --ff-only origin main`.
2. Back up the PostgreSQL database and media used by the stack. Run `alembic upgrade head` against that database before starting the updated API and worker. The current head is `0020_repeat_dispatch`, which follows `0019_sampled_review`.
3. In [app.py](../src/annotatiathon/app.py), Tomas registers the two routers alongside the existing routes:

   ```python
   from .review_queue_api import router as review_queue_router
   from .scoring_api import router as scoring_router

   app.include_router(review_queue_router)
   app.include_router(scoring_router)
   ```

The migration was rehearsed on a disposable benchmark database with existing records. Before and after the upgrade, row counts and checksums matched across 39 existing tables, including 520 jobs, 7,860 native image mappings and 360 reviews. Historical job listing and media lookup passed. No migration was run on Tomas's database.

## Wire the sampled review screen

Use the existing authenticated API client and CSRF header for writes. The endpoint paths below are relative to `/api/v1`. Organisers and monitors can use the shared review queue.

| UI action | API call and behavior |
| --- | --- |
| Show queue counts | `GET /review-queue/status` returns pending, active and completed counts overall and by team. |
| Start or resume review | `POST /review-queue/claim-next` returns `{"review": ...}`. A null review means there is no available sample. An active claim for that user is returned again on retry. |
| Render the frame | Load `review.image_url` as returned, using the signed-in session. Draw `review.annotation` over the image and use `review.classes` for class metadata. Show `source_filename`, `team_name` and `annotator_name`. Keep the annotation object's original array index for grading. |
| Renew the claim | `POST /review-queue/{review_id}/heartbeat` with `{"claim_token":"..."}`. Renew every 60 seconds while reviewing. The lease lasts 180 seconds, and the response contains the new `expires_at` in Unix seconds. On a 409 for a lost or expired claim, clear it and let the monitor claim again. |
| Complete and continue | `POST /review-queue/{review_id}/complete` with `action: "next"`. Use the next review from the response, or show the empty queue state if it is null. |
| Complete and pause | Use the same completion endpoint with `action: "pause"`. Stop renewing the completed claim and wait for the monitor to resume. |
| Leave without grading | `POST /review-queue/{review_id}/release` with the claim token. An abandoned lease expires automatically. |

Example completion request for a frame with two objects:

```json
{
  "claim_token": "11111111-1111-4111-8111-111111111111",
  "action": "next",
  "verdicts": [
    {"object_index": 0, "correct": true},
    {"object_index": 1, "correct": false, "reason": "wrong_class", "corrected_class": "crown"}
  ],
  "missing_classes": []
}
```

Send exactly one verdict per object. Incorrect keypoints require `wrong_class`, `wrong_placement` or `sabotage`. A wrong-class verdict requires a different class of the same geometry. Incorrect boxes can omit a reason. The optional `missing_classes` list records missing objects separately from existing objects graded incorrect. The completion response contains `completed` and `review`.

## Wire rubric and rankings

- `GET /scoring/rubric` returns `version` and `weights` for organisers and monitors. A claim carries the rubric version used for its grade.
- `PUT /scoring/rubric` is organiser-only. Send the full set of top-level integer weights: `keypoint`, `box`, `wrong_class`, `wrong_placement`, and `sabotage`. Saving creates a new version.
- `GET /rankings/teams` returns `items` in rank order for organisers and monitors. Show the correct box and keypoint counts, combined correct objects, weighted score, reviewed and available samples, missing objects, and errors by reason. The ranking uses the original review round.

## Smoke test after wiring

Use a disposable test event with a `shared_30_v1` job that reaches review and has a saved annotation snapshot. The worker populates the queue from saved submissions. Have two monitors claim distinct samples, check the image and overlay, complete a review, and check that the team ranking updates once. A test event needs at least two saved samples to exercise simultaneous claims in the UI.

API details: [review queue](../src/annotatiathon/review_queue_api.py) and [scoring](../src/annotatiathon/scoring_api.py).
