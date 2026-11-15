"""Refresh displayed native status independently from Nightjar review decisions."""
import logging

from sqlalchemy import or_, select, update

from .models import Event, LabelingJob, NativeJobBinding, epoch

logger = logging.getLogger(__name__)


def refresh_idle_bindings(db, api, *, limit=25, max_age=300):
    """Bounded remote reads; never reopen a packet or change a review outcome.

    Do not hold SQL row locks while reading Supervisely. A correction can
    replace the native ID during the read, so the update compares that ID too.
    """
    cutoff = epoch() - max_age
    candidates = list(db.execute(select(
        NativeJobBinding.id, NativeJobBinding.native_job_id,
        LabelingJob.native_dataset_id,
    ).join(LabelingJob, LabelingJob.id == NativeJobBinding.job_id)
      .join(Event, Event.id == NativeJobBinding.event_id)
      .where(Event.status == "active",
             LabelingJob.status.in_(["on_review", "needs_correction", "completed"]),
             or_(NativeJobBinding.last_synced_at.is_(None),
                 NativeJobBinding.last_synced_at < cutoff))
      .order_by(NativeJobBinding.last_synced_at.asc().nulls_first(), NativeJobBinding.id)
      .limit(limit)))
    db.commit()
    refreshed = 0
    for binding_id, native_id, dataset_id in candidates:
        try:
            info = api.labeling_job.get_info_by_id(native_id)
            if info is not None and (info.id != native_id or info.dataset_id != dataset_id):
                raise ValueError("native_job_dataset_changed")
            result = db.execute(update(NativeJobBinding).where(
                NativeJobBinding.id == binding_id,
                NativeJobBinding.native_job_id == native_id,
            ).values(native_status=info.status if info else "unavailable", last_synced_at=epoch()))
            db.commit()
            refreshed += result.rowcount
        except Exception as exc:
            db.rollback()
            logger.warning("Native status cache refresh failed for job %s (%s)",
                           native_id, type(exc).__name__)
    return refreshed
