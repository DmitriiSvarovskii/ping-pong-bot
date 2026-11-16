"""Committed PostgreSQL concurrency tests for rating result persistence."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.matches.domain import MatchContext, MatchStatus
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchSetModel,
)
from pingstats.modules.ratings.application import (
    RatingPeriodCloser,
    RatingPeriodNotCalculatingError,
    RatingPeriodNotPublishableError,
    RatingPeriodPublisher,
    RatingPeriodResultPersister,
    RatingPublicationChronologyError,
    RatingWorkflowOrchestrator,
)
from pingstats.modules.ratings.domain import (
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildStatus,
)
from pingstats.modules.ratings.infrastructure.models import (
    RatingModel,
    RatingPeriodMatchInputModel,
    RatingPeriodModel,
    RatingPeriodResultModel,
    RatingRebuildJobModel,
)
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = [
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
        reason="requires an explicitly selected migrated local PostgreSQL database",
    ),
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
        reason="requires temporary committed rows in the local development database",
    ),
]


def make_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        user_id,
        None,
        name,
        "PersistenceTest",
        f"result_persistence_{user_id.hex}",
        None,
        now,
        now,
        None,
    )


def make_period(starts_at: datetime, status: RatingPeriodStatus) -> RatingPeriod:
    return RatingPeriod(
        id=uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=status,
        algorithm="glicko2",
        algorithm_version="1",
        parameters={
            "initial_rating": 1500.0,
            "initial_rd": 350.0,
            "initial_volatility": 0.06,
            "tau": 0.5,
            "max_rd": 350.0,
            "convergence_epsilon": 0.000001,
            "max_iterations": 100,
        },
        calculation_version=1,
        calculated_version=None,
        published_version=None,
        published_at=None,
        created_at=starts_at,
        updated_at=starts_at,
        version=1,
    )


async def create_fixture(
    database: Database,
    *,
    starts_at: datetime,
    status: RatingPeriodStatus,
) -> tuple[RatingPeriod, UUID, UUID, UUID]:
    first, second, match_id = uuid4(), uuid4(), uuid4()
    async with database.session_factory() as session:
        users = SqlAlchemyUserRepository(session)
        await users.create(make_user(first, "First"))
        await users.create(make_user(second, "Second"))
        session.add(
            MatchModel(
                id=match_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.COMPLETED,
                created_by=first,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                winner_id=first,
                started_at=starts_at,
                completed_at=starts_at + timedelta(hours=1),
                version=2,
            )
        )
        session.add_all(
            (
                MatchParticipantModel(id=uuid4(), match_id=match_id, user_id=first, position=1),
                MatchParticipantModel(id=uuid4(), match_id=match_id, user_id=second, position=2),
                MatchSetModel(
                    id=uuid4(),
                    match_id=match_id,
                    set_number=1,
                    player1_score=11,
                    player2_score=5,
                ),
            )
        )
        await session.commit()
    period = make_period(starts_at, status)
    item = RatingPeriodMatchInput(
        period_id=period.id,
        calculation_version=1,
        match_id=match_id,
        source_match_version=2,
        correction_revision_id=None,
        result_fingerprint="a" * 64,
        player1_id=first,
        player2_id=second,
        score1=Decimal("1.00"),
        score2=Decimal("0.00"),
        completed_at=starts_at + timedelta(hours=1),
        created_at=starts_at + timedelta(hours=1),
    )
    async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(item)
        await uow.commit()
    return period, first, second, match_id


async def cleanup(
    database: Database,
    period_id: UUID,
    user_ids: tuple[UUID, UUID],
    match_id: UUID,
) -> None:
    async with database.session_factory() as session:
        await session.execute(delete(RatingModel).where(RatingModel.user_id.in_(user_ids)))
        await session.execute(
            delete(RatingPeriodResultModel).where(RatingPeriodResultModel.period_id == period_id)
        )
        await session.execute(
            delete(RatingPeriodMatchInputModel).where(
                RatingPeriodMatchInputModel.period_id == period_id
            )
        )
        await session.execute(delete(RatingPeriodModel).where(RatingPeriodModel.id == period_id))
        await session.execute(delete(MatchSetModel).where(MatchSetModel.match_id == match_id))
        await session.execute(
            delete(MatchParticipantModel).where(MatchParticipantModel.match_id == match_id)
        )
        await session.execute(delete(MatchModel).where(MatchModel.id == match_id))
        await session.execute(delete(UserModel).where(UserModel.id.in_(user_ids)))
        await session.commit()


async def test_concurrent_persistence_creates_one_complete_snapshot() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=datetime(1880, 1, 1, tzinfo=UTC),
            status=RatingPeriodStatus.CALCULATING,
        )
        service = RatingPeriodResultPersister()
        results = await asyncio.gather(
            *(
                service.persist(
                    SqlAlchemyRatingsUnitOfWork(database.session_factory),
                    period_id=period.id,
                    expected_calculation_version=1,
                )
                for _ in range(3)
            )
        )

        async with database.session_factory() as session:
            stored_period = await session.get(RatingPeriodModel, period.id)
            count = await session.scalar(
                select(func.count())
                .select_from(RatingPeriodResultModel)
                .where(RatingPeriodResultModel.period_id == period.id)
            )
        assert results[0] == results[1] == results[2]
        assert stored_period is not None and stored_period.calculated_version == 1
        assert stored_period.version == 2
        assert count == 2
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_publication_of_one_period_is_idempotent() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=datetime(1882, 1, 1, tzinfo=UTC),
            status=RatingPeriodStatus.CALCULATING,
        )
        await RatingPeriodResultPersister().persist(
            SqlAlchemyRatingsUnitOfWork(database.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )
        service = RatingPeriodPublisher()
        published = await asyncio.gather(
            *(
                service.publish(
                    SqlAlchemyRatingsUnitOfWork(database.session_factory),
                    period_id=period.id,
                    expected_calculation_version=1,
                )
                for _ in range(3)
            )
        )

        async with database.session_factory() as session:
            stored_period = await session.get(RatingPeriodModel, period.id)
            ratings = (
                await session.scalars(
                    select(RatingModel).where(RatingModel.user_id.in_((first, second)))
                )
            ).all()
        assert published[0] == published[1] == published[2]
        assert stored_period is not None and stored_period.status is RatingPeriodStatus.PUBLISHED
        assert stored_period.version == 3
        assert len(ratings) == 2
        assert {item.version for item in ratings} == {1}
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_persistence_and_publication_are_serialized() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=datetime(1883, 1, 1, tzinfo=UTC),
            status=RatingPeriodStatus.CALCULATING,
        )
        persistence, publication = await asyncio.gather(
            RatingPeriodResultPersister().persist(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=period.id,
                expected_calculation_version=1,
            ),
            RatingPeriodPublisher().publish(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=period.id,
                expected_calculation_version=1,
            ),
            return_exceptions=True,
        )
        assert not isinstance(persistence, Exception)
        if isinstance(publication, RatingPeriodNotPublishableError):
            publication = await RatingPeriodPublisher().publish(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=period.id,
                expected_calculation_version=1,
            )
        assert not isinstance(publication, Exception)

        async with database.session_factory() as session:
            stored_period = await session.get(RatingPeriodModel, period.id)
            count = await session.scalar(
                select(func.count())
                .select_from(RatingModel)
                .where(RatingModel.user_id.in_((first, second)))
            )
        assert stored_period is not None and stored_period.status is RatingPeriodStatus.PUBLISHED
        assert count == 2
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_publication_of_different_periods_preserves_order() -> None:
    database = Database.from_settings(Settings())
    first_period: RatingPeriod | None = None
    second_period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        starts_at = datetime(1884, 1, 1, tzinfo=UTC)
        first_period, first, second, match_id = await create_fixture(
            database,
            starts_at=starts_at,
            status=RatingPeriodStatus.CALCULATING,
        )
        persister = RatingPeriodResultPersister()
        await persister.persist(
            SqlAlchemyRatingsUnitOfWork(database.session_factory),
            period_id=first_period.id,
            expected_calculation_version=1,
        )
        second_period = make_period(starts_at + timedelta(days=1), RatingPeriodStatus.CALCULATING)
        second_input = RatingPeriodMatchInput(
            period_id=second_period.id,
            calculation_version=1,
            match_id=match_id,
            source_match_version=2,
            correction_revision_id=None,
            result_fingerprint="b" * 64,
            player1_id=first,
            player2_id=second,
            score1=Decimal("1.00"),
            score2=Decimal("0.00"),
            completed_at=second_period.starts_at + timedelta(hours=1),
            created_at=second_period.starts_at + timedelta(hours=1),
        )
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            await uow.periods.add(second_period)
            await uow.inputs.add(second_input)
            await uow.commit()
        await persister.persist(
            SqlAlchemyRatingsUnitOfWork(database.session_factory),
            period_id=second_period.id,
            expected_calculation_version=1,
        )

        publisher = RatingPeriodPublisher()
        first_result, second_result = await asyncio.gather(
            publisher.publish(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=first_period.id,
                expected_calculation_version=1,
            ),
            publisher.publish(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=second_period.id,
                expected_calculation_version=1,
            ),
            return_exceptions=True,
        )
        assert not isinstance(first_result, Exception)
        if isinstance(second_result, RatingPublicationChronologyError):
            second_result = await publisher.publish(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=second_period.id,
                expected_calculation_version=1,
            )
        assert not isinstance(second_result, Exception)

        async with database.session_factory() as session:
            periods = (
                await session.scalars(
                    select(RatingPeriodModel).where(
                        RatingPeriodModel.id.in_((first_period.id, second_period.id))
                    )
                )
            ).all()
            ratings = (
                await session.scalars(
                    select(RatingModel).where(RatingModel.user_id.in_((first, second)))
                )
            ).all()
        assert {item.status for item in periods} == {RatingPeriodStatus.PUBLISHED}
        assert {item.as_of_period_id for item in ratings} == {second_period.id}
        assert {item.rated_matches_count for item in ratings} == {2}
    finally:
        if second_period is not None:
            async with database.session_factory() as session:
                await session.execute(
                    delete(RatingModel).where(RatingModel.user_id.in_((first, second)))
                )
                await session.execute(
                    delete(RatingPeriodResultModel).where(
                        RatingPeriodResultModel.period_id == second_period.id
                    )
                )
                await session.execute(
                    delete(RatingPeriodMatchInputModel).where(
                        RatingPeriodMatchInputModel.period_id == second_period.id
                    )
                )
                await session.execute(
                    delete(RatingPeriodModel).where(RatingPeriodModel.id == second_period.id)
                )
                await session.commit()
        if first_period is not None:
            await cleanup(database, first_period.id, (first, second), match_id)
        await database.dispose()


async def test_rating_workflow_runs_full_postgresql_lifecycle() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        starts_at = datetime(1885, 1, 1, tzinfo=UTC)
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=starts_at,
            status=RatingPeriodStatus.OPEN,
        )
        published = await RatingWorkflowOrchestrator(
            clock=lambda: starts_at + timedelta(days=1, minutes=5)
        ).run(lambda: SqlAlchemyRatingsUnitOfWork(database.session_factory))

        async with database.session_factory() as session:
            stored = await session.get(RatingPeriodModel, period.id)
            ratings = (
                await session.scalars(
                    select(RatingModel).where(RatingModel.user_id.in_((first, second)))
                )
            ).all()
        assert tuple(item.id for item in published) == (period.id,)
        assert stored is not None and stored.status is RatingPeriodStatus.PUBLISHED
        assert stored.calculated_version == stored.calculation_version == 1
        assert len(ratings) == 2
        assert {item.rated_matches_count for item in ratings} == {1}
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_rating_workflows_do_not_double_publish() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        starts_at = datetime(1886, 1, 1, tzinfo=UTC)
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=starts_at,
            status=RatingPeriodStatus.OPEN,
        )
        now = starts_at + timedelta(days=1, minutes=5)
        await asyncio.gather(
            *(
                RatingWorkflowOrchestrator(clock=lambda: now).run(
                    lambda: SqlAlchemyRatingsUnitOfWork(database.session_factory)
                )
                for _ in range(3)
            )
        )

        async with database.session_factory() as session:
            stored = await session.get(RatingPeriodModel, period.id)
            ratings = (
                await session.scalars(
                    select(RatingModel).where(RatingModel.user_id.in_((first, second)))
                )
            ).all()
            result_count = await session.scalar(
                select(func.count())
                .select_from(RatingPeriodResultModel)
                .where(RatingPeriodResultModel.period_id == period.id)
            )
        assert stored is not None and stored.status is RatingPeriodStatus.PUBLISHED
        assert stored.version == 4
        assert result_count == 2
        assert len(ratings) == 2
        assert {item.rated_matches_count for item in ratings} == {1}
        assert {item.version for item in ratings} == {1}
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_closer_and_persistence_never_store_stale_results() -> None:
    database = Database.from_settings(Settings())
    period: RatingPeriod | None = None
    first = second = match_id = uuid4()
    try:
        starts_at = datetime(1881, 1, 1, tzinfo=UTC)
        period, first, second, match_id = await create_fixture(
            database,
            starts_at=starts_at,
            status=RatingPeriodStatus.OPEN,
        )
        service = RatingPeriodResultPersister()
        closer_result, persistence_result = await asyncio.gather(
            RatingPeriodCloser().close_period(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                starts_at=starts_at,
                now=starts_at + timedelta(days=1, minutes=5),
            ),
            service.persist(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=period.id,
                expected_calculation_version=1,
            ),
            return_exceptions=True,
        )
        assert not isinstance(closer_result, Exception)
        if isinstance(persistence_result, RatingPeriodNotCalculatingError):
            persistence_result = await service.persist(
                SqlAlchemyRatingsUnitOfWork(database.session_factory),
                period_id=period.id,
                expected_calculation_version=1,
            )
        assert not isinstance(persistence_result, Exception)

        async with database.session_factory() as session:
            stored_period = await session.get(RatingPeriodModel, period.id)
            count = await session.scalar(
                select(func.count())
                .select_from(RatingPeriodResultModel)
                .where(RatingPeriodResultModel.period_id == period.id)
            )
        assert stored_period is not None
        assert stored_period.status is RatingPeriodStatus.CALCULATING
        assert stored_period.calculated_version == 1
        assert count == 2
    finally:
        if period is not None:
            await cleanup(database, period.id, (first, second), match_id)
        await database.dispose()


async def test_concurrent_rebuild_job_creation_allows_only_one_active_job() -> None:
    database = Database.from_settings(Settings())
    starts_at = datetime(1886, 1, 1, tzinfo=UTC)
    first_period = make_period(starts_at, RatingPeriodStatus.PUBLISHED)
    second_period = make_period(starts_at + timedelta(days=1), RatingPeriodStatus.PUBLISHED)
    now = datetime.now(UTC)

    def make_job() -> RatingRebuildJob:
        return RatingRebuildJob(
            id=uuid4(),
            from_period_id=first_period.id,
            through_period_id=second_period.id,
            status=RatingRebuildStatus.PENDING,
            singleton_scope=1,
            created_at=now,
            started_at=None,
            ready_at=None,
            completed_at=None,
            error_code=None,
            version=1,
        )

    async def create(job: RatingRebuildJob) -> RatingRebuildJob:
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            stored = await uow.rebuild_jobs.add(job)
            await uow.commit()
            return stored

    try:
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            await uow.periods.add(first_period)
            await uow.periods.add(second_period)
            await uow.commit()

        results = await asyncio.gather(
            create(make_job()),
            create(make_job()),
            return_exceptions=True,
        )

        assert sum(isinstance(item, RatingRebuildJob) for item in results) == 1
        assert sum(isinstance(item, Exception) for item in results) == 1
        async with database.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(RatingRebuildJobModel)
                .where(RatingRebuildJobModel.from_period_id == first_period.id)
            )
        assert count == 1
    finally:
        async with database.session_factory() as session:
            await session.execute(
                delete(RatingRebuildJobModel).where(
                    RatingRebuildJobModel.from_period_id == first_period.id
                )
            )
            await session.execute(
                delete(RatingPeriodModel).where(
                    RatingPeriodModel.id.in_((first_period.id, second_period.id))
                )
            )
            await session.commit()
        await database.dispose()
