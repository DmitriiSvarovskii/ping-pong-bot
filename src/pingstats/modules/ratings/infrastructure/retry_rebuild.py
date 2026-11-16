"""Internal command for explicitly retrying one durable failed rating rebuild."""

import argparse
import asyncio
from uuid import UUID

from pingstats.core.config import Settings
from pingstats.core.logging import configure_logging
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.ratings.application import (
    RatingRebuildOrchestrator,
    RatingWorkflowOrchestrator,
)
from pingstats.modules.ratings.domain import RatingRebuildStatus
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork


async def retry_rebuild(job_id: UUID, settings: Settings | None = None) -> None:
    resolved = settings or Settings()
    configure_logging(resolved.log_level)
    database = Database.from_settings(resolved)

    def factory() -> SqlAlchemyRatingsUnitOfWork:
        return SqlAlchemyRatingsUnitOfWork(database.session_factory)

    try:
        orchestrator = RatingRebuildOrchestrator()
        await orchestrator.retry_failed(factory, job_id=job_id)
        result = await orchestrator.run_once(factory)
        if result is not None and result.status is RatingRebuildStatus.COMPLETED:
            await RatingWorkflowOrchestrator().run(factory)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Retry one failed PingStats rating rebuild")
    parser.add_argument("job_id", type=UUID)
    arguments = parser.parse_args()
    asyncio.run(retry_rebuild(arguments.job_id))


if __name__ == "__main__":
    main()
