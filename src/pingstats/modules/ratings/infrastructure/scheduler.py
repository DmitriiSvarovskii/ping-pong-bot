"""Lightweight process-local scheduler for the complete rating workflow."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

from pingstats.core.exceptions import ApplicationError
from pingstats.modules.ratings.application import (
    RatingRebuildOrchestrator,
    RatingWorkflowOrchestrator,
)
from pingstats.modules.ratings.application.closer import RatingsUnitOfWorkFactory
from pingstats.modules.ratings.domain import RatingRebuildStatus

logger = logging.getLogger(__name__)

RATING_PERIOD_CLOSE_TIME = time(hour=0, minute=5, tzinfo=UTC)
REBUILD_DISCOVERY_SECONDS = 30.0
SCHEDULER_FAILURE_RETRY_SECONDS = 5.0
SCHEDULER_MAX_FAST_RETRIES = 3
SCHEDULER_DEGRADED_RETRY_SECONDS = 300.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingSchedulerWakeup:
    """Process-local hint; durable PostgreSQL state remains the source of truth."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def notify(self) -> None:
        self._event.set()

    async def wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except TimeoutError:
            pass
        finally:
            self._event.clear()


class RatingPeriodScheduler:
    """Run rebuild recovery and normal rating catch-up in one local task."""

    def __init__(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        workflow: RatingWorkflowOrchestrator | None = None,
        rebuild: RatingRebuildOrchestrator | None = None,
        wakeup: RatingSchedulerWakeup | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._uow_factory = uow_factory
        self._workflow = workflow or RatingWorkflowOrchestrator(clock=clock)
        self._rebuild = rebuild or RatingRebuildOrchestrator(clock=clock)
        self._wakeup = wakeup
        self._clock = clock
        self._sleep = sleep

    async def run_forever(self) -> None:
        infrastructure_failures = 0
        while True:
            try:
                await self.run_cycle()
                infrastructure_failures = 0
            except asyncio.CancelledError:
                raise
            except ApplicationError:
                logger.exception(
                    "Rating workflow blocked by a business conflict; intervention is required"
                )
            except Exception:
                infrastructure_failures += 1
                logger.exception("Rating workflow infrastructure cycle failed")

            delay = min(
                self.seconds_until_next_run(self._clock()),
                REBUILD_DISCOVERY_SECONDS,
            )
            if infrastructure_failures:
                if infrastructure_failures <= SCHEDULER_MAX_FAST_RETRIES:
                    delay = min(
                        delay,
                        SCHEDULER_FAILURE_RETRY_SECONDS * (2 ** (infrastructure_failures - 1)),
                    )
                else:
                    delay = SCHEDULER_DEGRADED_RETRY_SECONDS
            if self._wakeup is None:
                await self._sleep(delay)
            else:
                await self._wakeup.wait(delay)

    async def run_cycle(self) -> None:
        """Recover rebuild first, then resume the ordinary period workflow."""
        rebuild = await self._rebuild.run_once(self._uow_factory)
        if rebuild is not None and rebuild.status not in {
            RatingRebuildStatus.COMPLETED,
            RatingRebuildStatus.SUPERSEDED,
        }:
            return
        await self._workflow.run(self._uow_factory)

    @staticmethod
    def seconds_until_next_run(now: datetime) -> float:
        if now.tzinfo is None:
            raise ValueError("Scheduler clock must return a timezone-aware datetime")
        utc_now = now.astimezone(UTC)
        next_run = datetime.combine(utc_now.date(), RATING_PERIOD_CLOSE_TIME)
        if next_run <= utc_now:
            next_run += timedelta(days=1)
        return (next_run - utc_now).total_seconds()
