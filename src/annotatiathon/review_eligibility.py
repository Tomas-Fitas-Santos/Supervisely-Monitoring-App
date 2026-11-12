"""Only fully structurally complete jobs release frames to the monitor pool.

Per-frame snapshots may be staged during corrections, but *no* frames from a
30-frame job are reviewable until the job is fully complete. Legacy review
samples follow their established current-round and job-status policy.
"""
from sqlalchemy import and_, or_

from .models import LabelingJob, ReviewSample


def eligible_frame_review():
    return or_(
        and_(
            ReviewSample.validated_complete.is_(True),
            LabelingJob.annotation_snapshot_frames == LabelingJob.frame_count,
            LabelingJob.status.in_(("on_review", "completed")),
        ),
        and_(
            ReviewSample.validated_complete.is_not(True),
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")),
        ),
    )
