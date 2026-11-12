"""Recognize immutable accepted frames while their original job is corrected.

Older job-level reviews retain their original latest-round semantics. New
per-frame accepted reviews remain valid throughout retries of OTHER frames.
"""
from sqlalchemy import and_, or_

from .models import LabelingJob, ReviewSample


def eligible_frame_review():
    return or_(
        ReviewSample.validated_complete.is_(True),
        and_(
            ReviewSample.correction_round == LabelingJob.correction_round,
            LabelingJob.status.in_(("on_review", "completed")),
        ),
    )
