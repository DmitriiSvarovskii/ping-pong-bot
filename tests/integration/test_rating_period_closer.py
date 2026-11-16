"""PostgreSQL tests for safe rating period closing."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.ratings.application import RatingPeriodCloser
from pingstats.modules.ratings.domain import RatingPeriod, RatingPeriodStatus
from pingstats.modules.ratings.infrastructure.models import RatingPeriodModel
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


def make_period(starts_at: datetime) -> RatingPeriod:
    return RatingPeriod(
        id=uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=RatingPeriodStatus.OPEN,
        algorithm="glicko2",
        algorithm_version="1",
        parameters={"tau": 0.5},
        calculation_version=1,
        calculated_version=None,
        published_version=None,
        published_at=None,
        created_at=starts_at,
        updated_at=starts_at,
        version=1,
    )


@pytest.fixture
async def closer_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session_factory
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def test_closes_due_periods_catches_up_and_is_idempotent(
    closer_database: async_sessionmaker[AsyncSession],
) -> None:
    starts = [datetime(2001, 4, day, tzinfo=UTC) for day in (1, 2, 3)]
    early_start = datetime(2001, 4, 4, tzinfo=UTC)
    async with SqlAlchemyRatingsUnitOfWork(closer_database) as uow:
        for starts_at in (*starts, early_start):
            await uow.periods.add(make_period(starts_at))
        await uow.commit()

    closer = RatingPeriodCloser()
    closed = await closer.close_due(
        lambda: SqlAlchemyRatingsUnitOfWork(closer_database),
        now=datetime(2001, 4, 4, 0, 5, tzinfo=UTC),
    )
    repeated = await closer.close_due(
        lambda: SqlAlchemyRatingsUnitOfWork(closer_database),
        now=datetime(2001, 4, 4, 0, 5, tzinfo=UTC),
    )

    async with closer_database() as session:
        rows = (
            await session.scalars(
                select(RatingPeriodModel)
                .where(RatingPeriodModel.starts_at.in_((*starts, early_start)))
                .order_by(RatingPeriodModel.starts_at)
            )
        ).all()
    assert [period.starts_at for period in closed] == starts
    assert repeated == ()
    assert [row.status for row in rows[:3]] == [RatingPeriodStatus.CALCULATING] * 3
    assert [row.version for row in rows[:3]] == [2, 2, 2]
    assert rows[3].status is RatingPeriodStatus.OPEN
    assert rows[3].version == 1


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed rows in the local development database",
)
async def test_concurrent_closers_change_period_once() -> None:
    database = Database.from_settings(Settings())
    starts_at = datetime(2101, 3, 4, tzinfo=UTC)
    period_id: UUID | None = None
    try:
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            period = await uow.periods.add(make_period(starts_at))
            period_id = period.id
            await uow.commit()

        closer = RatingPeriodCloser()
        results = await asyncio.gather(
            *(
                closer.close_period(
                    SqlAlchemyRatingsUnitOfWork(database.session_factory),
                    starts_at=starts_at,
                    now=datetime(2101, 3, 5, 0, 5, tzinfo=UTC),
                )
                for _ in range(3)
            )
        )

        async with database.session_factory() as session:
            stored = await session.get(RatingPeriodModel, period_id)
        assert all(result is not None for result in results)
        assert stored is not None
        assert stored.status is RatingPeriodStatus.CALCULATING
        assert stored.version == 2
    finally:
        if period_id is not None:
            async with database.session_factory() as session:
                await session.execute(
                    delete(RatingPeriodModel).where(RatingPeriodModel.id == period_id)
                )
                await session.commit()
        await database.dispose()
