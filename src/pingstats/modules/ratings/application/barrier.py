"""Persistent rebuild barrier shared by normal rating workflow stages."""

from pingstats.modules.ratings.application.errors import RatingRebuildBarrierError
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.domain import RatingPeriod


async def require_period_not_blocked(uow: RatingsUnitOfWork, period: RatingPeriod) -> None:
    """Reject normal processing of any history dependent on an active rebuild."""
    jobs = getattr(uow, "rebuild_jobs", None)
    if jobs is None:
        return
    barrier = await jobs.get_publication_barrier()
    if barrier is None:
        return
    from_period = await uow.periods.get(barrier.from_period_id)
    if from_period is None or period.starts_at >= from_period.starts_at:
        raise RatingRebuildBarrierError
