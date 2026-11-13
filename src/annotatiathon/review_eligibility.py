"""Expose each structurally complete frame immediately, including partial jobs.

Accepted annotation JSON is immutable across correction rounds. The returned
Supervisely job contains ONLY missing/invalid frames while already validated
frames remain in Nightjar's frame-review queue. Legacy samples keep their
current-round and submission-state eligibility checks.
"""
from sqlalchemy import and_, or_

from .models import LabelingJob, ReviewSample

REVIEWABLE_ACCEPTED_STATUSES = (
    "needs_correction", "in_progress", "on_review", "completed",
)


def eligible_frame_review():
    return or_(
        and_(
            ReviewSample.validated_complete.is_(True),
            LabelingJob.annotation_snapshot_frames.is_not(None),
            LabelingJob.status.in_(REVIEWABLE_ACCEPTED_STATUSES),
        ),
        and_(
            ReviewSample.validated_complete.is_not(True),
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")),
        ),
    )
