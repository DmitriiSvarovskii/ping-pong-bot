"""PostgreSQL integration tests for versioned ratings persistence."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.leaderboard.infrastructure import SqlAlchemyLeaderboardRepository
from pingstats.modules.matches.domain import MatchContext, MatchRevisionOperation, MatchStatus
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchResultRevisionModel,
    MatchSetModel,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.ratings.application import (
    RatingBackfillConflictError,
    RatingCalculationSnapshotConflictError,
    RatingCorrectionCoordinator,
    RatingPeriodCalculator,
    RatingPeriodPublisher,
    RatingPeriodResultPersister,
    RatingRebuildBarrierError,
    RatingRebuildCutover,
    RatingRebuildExecutor,
    RatingRebuildOrchestrator,
    UnsupportedRatingAlgorithmError,
)
from pingstats.modules.ratings.application.backfill import RatingHistoricalBackfill
from pingstats.modules.ratings.application.match_state import (
    MatchRatingState,
    RatingMatchStateService,
)
from pingstats.modules.ratings.application.status import RatingStatusService, RatingUpdateState
from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationRequest,
    RatingRecalculationStatus,
)
from pingstats.modules.ratings.infrastructure.models import (
    RatingModel,
    RatingPeriodMatchInputModel,
    RatingPeriodModel,
    RatingPeriodResultModel,
    RatingRebuildJobModel,
    RatingRebuildPeriodModel,
    RatingRecalculationRequestModel,
)
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingInputRepository,
    SqlAlchemyRatingPeriodRepository,
    SqlAlchemyRatingQueryRepository,
    SqlAlchemyRatingRepository,
    SqlAlchemyRatingResultRepository,
)
from pingstats.modules.ratings.infrastructure.scheduler import RatingPeriodScheduler
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


@dataclass(frozen=True, slots=True)
class RatingFixture:
    session_factory: async_sessionmaker[AsyncSession]
    first_user_id: UUID
    second_user_id: UUID
    match_id: UUID
    revision_id: UUID


def make_user(user_id: UUID, first_name: str) -> User:
    now = datetime.now(UTC)
    return User(
        user_id,
        None,
        first_name,
        "RatingTest",
        f"rating_{user_id.hex}",
        None,
        now,
        now,
        None,
    )


def make_period(
    *,
    starts_at: datetime,
    period_id: UUID | None = None,
    algorithm_version: str = "1",
) -> RatingPeriod:
    now = datetime.now(UTC)
    return RatingPeriod(
        id=period_id or uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=RatingPeriodStatus.OPEN,
        algorithm="glicko2",
        algorithm_version=algorithm_version,
        parameters={
            "initial_rating": 1500.0,
            "initial_rd": 350.0,
            "initial_volatility": 0.06,
            "tau": 0.5,
        },
        calculation_version=1,
        calculated_version=None,
        published_version=None,
        published_at=None,
        created_at=now,
        updated_at=now,
        version=1,
    )


def make_input(
    fixture: RatingFixture,
    period_id: UUID,
    *,
    calculation_version: int = 1,
    fingerprint: str = "a" * 64,
) -> RatingPeriodMatchInput:
    now = datetime.now(UTC)
    return RatingPeriodMatchInput(
        period_id=period_id,
        calculation_version=calculation_version,
        match_id=fixture.match_id,
        source_match_version=2,
        correction_revision_id=fixture.revision_id,
        result_fingerprint=fingerprint,
        player1_id=fixture.first_user_id,
        player2_id=fixture.second_user_id,
        score1=Decimal("1.00"),
        score2=Decimal("0.00"),
        completed_at=now,
        created_at=now,
    )


def make_result(
    fixture: RatingFixture,
    period_id: UUID,
    *,
    calculation_version: int = 1,
) -> RatingPeriodResult:
    return RatingPeriodResult(
        id=uuid4(),
        period_id=period_id,
        calculation_version=calculation_version,
        user_id=fixture.first_user_id,
        rating_before=Decimal("1500.00000"),
        rating_after=Decimal("1512.34567"),
        rd_before=Decimal("350.00000"),
        rd_after=Decimal("290.12345"),
        volatility_before=Decimal("0.0600000000"),
        volatility_after=Decimal("0.0599990000"),
        games_count=1,
        is_active=True,
        created_at=datetime.now(UTC),
    )


async def add_historical_match(
    session: AsyncSession,
    *,
    match_id: UUID,
    first_user_id: UUID,
    second_user_id: UUID,
    completed_at: datetime,
    status: MatchStatus = MatchStatus.COMPLETED,
    is_ranked: bool = True,
) -> None:
    session.add(
        MatchModel(
            id=match_id,
            context=MatchContext.STANDALONE,
            status=status,
            created_by=first_user_id,
            points_to_win=11,
            win_by=2,
            sets_to_win=1,
            is_ranked=is_ranked,
            winner_id=first_user_id if status is MatchStatus.COMPLETED else None,
            started_at=completed_at - timedelta(minutes=20),
            completed_at=completed_at,
            version=2,
        )
    )
    session.add_all(
        (
            MatchParticipantModel(id=uuid4(), match_id=match_id, user_id=first_user_id, position=1),
            MatchParticipantModel(
                id=uuid4(), match_id=match_id, user_id=second_user_id, position=2
            ),
            MatchSetModel(
                id=uuid4(),
                match_id=match_id,
                set_number=1,
                player1_score=11,
                player2_score=7,
            ),
        )
    )


@pytest.fixture
async def rating_database() -> AsyncIterator[RatingFixture]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    first_user_id, second_user_id = uuid4(), uuid4()
    match_id, revision_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with session_factory() as session:
        users = SqlAlchemyUserRepository(session)
        await users.create(make_user(first_user_id, "First"))
        await users.create(make_user(second_user_id, "Second"))
        session.add(
            MatchModel(
                id=match_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.COMPLETED,
                created_by=first_user_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                winner_id=first_user_id,
                started_at=now,
                completed_at=now,
                version=2,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    id=uuid4(), match_id=match_id, user_id=first_user_id, position=1
                ),
                MatchParticipantModel(
                    id=uuid4(), match_id=match_id, user_id=second_user_id, position=2
                ),
            ]
        )
        session.add(
            MatchSetModel(
                id=uuid4(),
                match_id=match_id,
                set_number=1,
                player1_score=11,
                player2_score=4,
            )
        )
        session.add(
            MatchResultRevisionModel(
                id=revision_id,
                match_id=match_id,
                revision_number=1,
                changed_by=first_user_id,
                previous_result={"winner_id": str(second_user_id), "sets": []},
                new_result={"winner_id": str(first_user_id), "sets": []},
                reason="rating persistence fixture",
            )
        )
        await session.commit()

    try:
        yield RatingFixture(
            session_factory=session_factory,
            first_user_id=first_user_id,
            second_user_id=second_user_id,
            match_id=match_id,
            revision_id=revision_id,
        )
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def test_repositories_store_versions_update_state_and_rollback(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = make_period(starts_at=datetime(2095, 1, 1, tzinfo=UTC))
    now = datetime.now(UTC)
    rating = PublishedRating(
        user_id=fixture.first_user_id,
        rating=Decimal("1500.00000"),
        rd=Decimal("350.00000"),
        volatility=Decimal("0.0600000000"),
        as_of_period_id=None,
        rated_matches_count=0,
        updated_at=now,
        version=1,
    )
    request = RatingRecalculationRequest(
        id=uuid4(),
        from_period_id=period.id,
        caused_by_match_id=fixture.match_id,
        caused_by_revision_id=fixture.revision_id,
        rebuild_job_id=None,
        status=RatingRecalculationStatus.PENDING,
        created_at=now,
        started_at=None,
        completed_at=None,
        error_code=None,
    )

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.ratings.add(rating)
        await uow.inputs.add_all(
            [
                make_input(fixture, period.id),
                make_input(
                    fixture,
                    period.id,
                    calculation_version=2,
                    fingerprint="b" * 64,
                ),
            ]
        )
        await uow.results.add_all(
            [
                make_result(fixture, period.id),
                make_result(fixture, period.id, calculation_version=2),
            ]
        )
        await uow.recalculations.add(request)
        await uow.commit()

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored_rating = await uow.ratings.get(fixture.first_user_id, for_update=True)
        stored_period = await uow.periods.get_by_start(period.starts_at)
        inputs_v1 = await uow.inputs.list_for_period(period.id, 1)
        inputs_v2 = await uow.inputs.list_for_period(period.id, 2)
        results = await uow.results.list_for_user(fixture.first_user_id)
        stored_request = await uow.recalculations.get_by_revision(fixture.revision_id)

        assert stored_rating == rating
        assert stored_period == period
        assert len(inputs_v1) == len(inputs_v2) == 1
        assert {item.calculation_version for item in results} == {1, 2}
        assert stored_request == request

        updated_rating = await uow.ratings.update_published_state(
            user_id=fixture.first_user_id,
            rating=Decimal("1512.34567"),
            rd=Decimal("290.12345"),
            volatility=Decimal("0.0599990000"),
            as_of_period_id=period.id,
            rated_matches_count=1,
            expected_version=1,
        )
        updated_period = await uow.periods.update_state(
            period_id=period.id,
            status=RatingPeriodStatus.PUBLISHED,
            calculation_version=2,
            published_at=now,
            expected_version=1,
        )
        updated_request = await uow.recalculations.update_status(
            request_id=request.id,
            status=RatingRecalculationStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            error_code=None,
        )
        await uow.commit()

    assert updated_rating is not None and updated_rating.version == 2
    assert updated_rating.as_of_period_id == period.id
    assert updated_period is not None and updated_period.version == 2
    assert updated_period.status is RatingPeriodStatus.PUBLISHED
    assert updated_request is not None
    assert updated_request.status is RatingRecalculationStatus.COMPLETED

    rolled_back = make_period(starts_at=datetime(2095, 1, 2, tzinfo=UTC))
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(rolled_back)
        await uow.rollback()
    async with fixture.session_factory() as session:
        assert await session.get(RatingPeriodModel, rolled_back.id) is None


async def test_period_calculator_reads_previous_results_and_current_inputs(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    previous = replace(
        make_period(starts_at=datetime(2094, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
        calculated_version=1,
    )
    current = replace(
        make_period(starts_at=datetime(2094, 1, 2, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
    )
    first_previous = make_result(fixture, previous.id)
    second_previous = replace(
        first_previous,
        id=uuid4(),
        user_id=fixture.second_user_id,
        rating_before=Decimal("1400.00000"),
        rating_after=Decimal("1400.00000"),
        rd_before=Decimal("120.00000"),
        rd_after=Decimal("120.00000"),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(previous)
        await uow.periods.add(current)
        await uow.results.add_all((first_previous, second_previous))
        await uow.inputs.add(make_input(fixture, current.id))
        await uow.commit()

    calculated = await RatingPeriodCalculator().calculate(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=current.id,
    )
    players = {item.user_id: item for item in calculated.players}

    assert calculated.period_id == current.id
    assert players[fixture.first_user_id].rating_before == Decimal("1512.34567")
    assert players[fixture.second_user_id].rating_before == Decimal("1400.00000")
    assert players[fixture.first_user_id].games_count == 1
    assert players[fixture.second_user_id].games_count == 1
    async with fixture.session_factory() as session:
        stored_current_results = (
            await session.scalars(
                select(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == current.id
                )
            )
        ).all()
    assert stored_current_results == []


async def test_result_persistence_is_atomic_idempotent_and_detects_conflict(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = replace(
        make_period(starts_at=datetime(2000, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(make_input(fixture, period.id))
        await uow.commit()

    service = RatingPeriodResultPersister()
    first = await service.persist(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=period.id,
        expected_calculation_version=1,
    )
    repeated = await service.persist(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=period.id,
        expected_calculation_version=1,
    )

    async with fixture.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        stored_results = (
            await session.scalars(
                select(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period.id
                )
            )
        ).all()
        current_ratings = (
            await session.scalars(
                select(RatingModel).where(
                    RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id))
                )
            )
        ).all()
    assert repeated == first
    assert stored_period is not None and stored_period.calculated_version == 1
    assert stored_period.status is RatingPeriodStatus.CALCULATING
    assert stored_period.published_at is None
    assert len(stored_results) == len(first.players) == 2
    assert current_ratings == []

    async with fixture.session_factory() as session:
        stored_results[0].rating_after += Decimal("1.00000")
        session.add(stored_results[0])
        await session.commit()
    with pytest.raises(RatingCalculationSnapshotConflictError):
        await service.persist(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )


async def test_empty_period_persists_completion_marker_without_results(
    rating_database: RatingFixture,
) -> None:
    period = replace(
        make_period(starts_at=datetime(1900, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
    )
    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        await uow.periods.add(period)
        await uow.commit()

    calculated = await RatingPeriodResultPersister().persist(
        SqlAlchemyRatingsUnitOfWork(rating_database.session_factory),
        period_id=period.id,
        expected_calculation_version=1,
    )

    async with rating_database.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        stored_results = (
            await session.scalars(
                select(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period.id
                )
            )
        ).all()
    assert calculated.players == ()
    assert stored_results == []
    assert stored_period is not None and stored_period.calculated_version == 1


async def test_rating_publication_atomically_creates_current_ratings(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = replace(
        make_period(starts_at=datetime(1700, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
        calculated_version=1,
    )
    first = make_result(fixture, period.id)
    second = replace(
        first,
        id=uuid4(),
        user_id=fixture.second_user_id,
        rating_after=Decimal("1487.65433"),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(make_input(fixture, period.id))
        await uow.results.add_all((first, second))
        await uow.commit()

    published = await RatingPeriodPublisher().publish(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=period.id,
        expected_calculation_version=1,
    )

    async with fixture.session_factory() as session:
        ratings = (
            await session.scalars(
                select(RatingModel)
                .where(RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id)))
                .order_by(RatingModel.user_id)
            )
        ).all()
        stored_results = (
            await session.scalars(
                select(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period.id
                )
            )
        ).all()
    assert published.status is RatingPeriodStatus.PUBLISHED
    assert published.published_at is not None
    assert len(ratings) == len(stored_results) == 2
    assert {item.as_of_period_id for item in ratings} == {period.id}
    assert {item.rated_matches_count for item in ratings} == {1}
    assert {item.id for item in stored_results} == {first.id, second.id}


class FailingRatingPublicationRepository(SqlAlchemyRatingRepository):
    def __init__(self, session):
        super().__init__(session)
        self._adds = 0

    async def add(self, rating):
        self._adds += 1
        stored = await super().add(rating)
        if self._adds == 2:
            raise RuntimeError("injected rating publication failure")
        return stored


class FailingRatingPublicationUow(SqlAlchemyRatingsUnitOfWork):
    async def __aenter__(self) -> "FailingRatingPublicationUow":
        await super().__aenter__()
        self.ratings = FailingRatingPublicationRepository(self.session)
        return self


class FailingCutoverPeriodRepository(SqlAlchemyRatingPeriodRepository):
    async def activate_rebuild_version(self, **kwargs):
        del kwargs
        raise RuntimeError("injected cutover failure")


class FailingCutoverUow(SqlAlchemyRatingsUnitOfWork):
    async def __aenter__(self) -> "FailingCutoverUow":
        await super().__aenter__()
        self.periods = FailingCutoverPeriodRepository(self.session)
        return self


async def test_rating_publication_failure_rolls_back_ratings_and_period(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = replace(
        make_period(starts_at=datetime(1701, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
        calculated_version=1,
    )
    first = make_result(fixture, period.id)
    second = replace(first, id=uuid4(), user_id=fixture.second_user_id)
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(make_input(fixture, period.id))
        await uow.results.add_all((first, second))
        await uow.commit()

    with pytest.raises(RuntimeError, match="injected rating publication failure"):
        await RatingPeriodPublisher().publish(
            FailingRatingPublicationUow(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )

    async with fixture.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        ratings = (
            await session.scalars(
                select(RatingModel).where(
                    RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id))
                )
            )
        ).all()
    assert ratings == []
    assert stored_period is not None
    assert stored_period.status is RatingPeriodStatus.CALCULATING
    assert stored_period.published_at is None


class FailingRatingResultRepository(SqlAlchemyRatingResultRepository):
    async def add_all(self, results):
        await super().add_all(results[:1])
        raise RuntimeError("injected result persistence failure")


class FailingResultPersistenceUow(SqlAlchemyRatingsUnitOfWork):
    async def __aenter__(self) -> "FailingResultPersistenceUow":
        await super().__aenter__()
        self.results = FailingRatingResultRepository(self.session)
        return self


async def test_result_persistence_failure_rolls_back_rows_and_marker(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = replace(
        make_period(starts_at=datetime(2001, 1, 1, tzinfo=UTC)),
        status=RatingPeriodStatus.CALCULATING,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(make_input(fixture, period.id))
        await uow.commit()

    with pytest.raises(RuntimeError, match="injected result persistence failure"):
        await RatingPeriodResultPersister().persist(
            FailingResultPersistenceUow(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )

    async with fixture.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        stored_results = (
            await session.scalars(
                select(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period.id
                )
            )
        ).all()
    assert stored_results == []
    assert stored_period is not None and stored_period.calculated_version is None


async def test_sparse_calendar_workflow_persists_and_publishes_without_empty_periods(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    previous_start = datetime(2002, 11, 1, tzinfo=UTC)
    current_start = datetime(2002, 11, 10, tzinfo=UTC)
    previous = replace(
        make_period(starts_at=previous_start),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=previous_start + timedelta(days=1, minutes=5),
    )
    current = replace(
        make_period(starts_at=current_start, algorithm_version="2"),
        status=RatingPeriodStatus.CALCULATING,
    )
    previous_results = (
        replace(
            make_result(fixture, previous.id),
            rating_before=Decimal("1600.00000"),
            rating_after=Decimal("1600.00000"),
            rd_before=Decimal("80.00000"),
            rd_after=Decimal("80.00000"),
            volatility_before=Decimal("0.0600000000"),
            volatility_after=Decimal("0.0600000000"),
        ),
        replace(
            make_result(fixture, previous.id),
            id=uuid4(),
            user_id=fixture.second_user_id,
            rating_before=Decimal("1400.00000"),
            rating_after=Decimal("1400.00000"),
            rd_before=Decimal("120.00000"),
            rd_after=Decimal("120.00000"),
            volatility_before=Decimal("0.0500000000"),
            volatility_after=Decimal("0.0500000000"),
        ),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(previous)
        await uow.periods.add(current)
        await uow.results.add_all(previous_results)
        await uow.inputs.add(
            replace(
                make_input(fixture, current.id),
                completed_at=current_start + timedelta(hours=1),
                created_at=current_start + timedelta(hours=1),
            )
        )
        for result in previous_results:
            await uow.ratings.add(
                PublishedRating(
                    user_id=result.user_id,
                    rating=result.rating_after,
                    rd=result.rd_after,
                    volatility=result.volatility_after,
                    as_of_period_id=previous.id,
                    rated_matches_count=1,
                    updated_at=previous.published_at,
                    version=1,
                )
            )
        await uow.commit()

    calculation = await RatingPeriodResultPersister().persist(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=current.id,
        expected_calculation_version=1,
    )
    players = {item.user_id: item for item in calculation.players}
    assert players[fixture.first_user_id].rating_before == Decimal("1600.00000")
    assert players[fixture.first_user_id].rd_before == Decimal("80.00000")
    assert players[fixture.first_user_id].rd_after > Decimal("80.00000")

    await RatingPeriodPublisher().publish(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        period_id=current.id,
        expected_calculation_version=1,
    )

    async with fixture.session_factory() as session:
        stored_current = await session.get(RatingPeriodModel, current.id)
        period_count = await session.scalar(
            select(func.count())
            .select_from(RatingPeriodModel)
            .where(
                RatingPeriodModel.starts_at >= previous_start,
                RatingPeriodModel.starts_at <= current_start,
            )
        )
        ratings = (
            await session.scalars(
                select(RatingModel).where(
                    RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id))
                )
            )
        ).all()
    assert period_count == 2
    assert stored_current is not None and stored_current.published_version == 1
    assert {item.as_of_period_id for item in ratings} == {current.id}


async def test_period_utc_boundaries_overlap_and_repeat_are_rejected(
    rating_database: RatingFixture,
) -> None:
    factory = rating_database.session_factory
    base = datetime(2096, 1, 1, tzinfo=UTC)
    async with factory() as session:
        session.add(
            RatingPeriodModel(
                id=uuid4(),
                starts_at=base + timedelta(hours=1),
                ends_at=base + timedelta(days=1, hours=1),
                status=RatingPeriodStatus.OPEN,
                algorithm="glicko2",
                algorithm_version="1",
                parameters={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with factory() as session:
        await session.execute(text("SET LOCAL TIME ZONE 'Pacific/Honolulu'"))
        repository = SqlAlchemyRatingPeriodRepository(session)
        first = make_period(starts_at=base)
        await repository.add(first)
        await session.commit()

    async with factory() as session:
        repository = SqlAlchemyRatingPeriodRepository(session)
        with pytest.raises(IntegrityError):
            await repository.add(make_period(starts_at=base))
        await session.rollback()

    async with factory() as session:
        session.add(
            RatingPeriodModel(
                id=uuid4(),
                starts_at=base - timedelta(days=1),
                ends_at=base + timedelta(hours=12),
                status=RatingPeriodStatus.OPEN,
                algorithm="glicko2",
                algorithm_version="1",
                parameters={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_calculated_version_cannot_exceed_current_calculation_version(
    rating_database: RatingFixture,
) -> None:
    invalid = replace(
        make_period(starts_at=datetime(2096, 2, 1, tzinfo=UTC)),
        calculated_version=2,
    )
    async with rating_database.session_factory() as session:
        with pytest.raises(IntegrityError):
            await SqlAlchemyRatingPeriodRepository(session).add(invalid)
        await session.rollback()


async def test_input_result_rating_constraints_and_fk_restrict(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = make_period(starts_at=datetime(2097, 1, 1, tzinfo=UTC))
    async with fixture.session_factory() as session:
        await SqlAlchemyRatingPeriodRepository(session).add(period)
        await session.commit()

    async with fixture.session_factory() as session:
        session.add(
            RatingModel(
                user_id=fixture.first_user_id,
                rating=Decimal("-1"),
                rd=Decimal("350"),
                volatility=Decimal("0.06"),
                rated_matches_count=0,
                version=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    invalid_input = make_input(fixture, period.id)
    async with fixture.session_factory() as session:
        session.add(
            RatingPeriodMatchInputModel(
                period_id=invalid_input.period_id,
                calculation_version=invalid_input.calculation_version,
                match_id=invalid_input.match_id,
                source_match_version=invalid_input.source_match_version,
                correction_revision_id=invalid_input.correction_revision_id,
                result_fingerprint=invalid_input.result_fingerprint,
                player1_id=invalid_input.player1_id,
                player2_id=invalid_input.player2_id,
                score1=Decimal("0.50"),
                score2=Decimal("0.00"),
                completed_at=invalid_input.completed_at,
                created_at=invalid_input.created_at,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with fixture.session_factory() as session:
        repository = SqlAlchemyRatingInputRepository(session)
        await repository.add(make_input(fixture, period.id))
        with pytest.raises(IntegrityError):
            await repository.add(make_input(fixture, period.id, fingerprint="c" * 64))
        await session.rollback()

    async with fixture.session_factory() as session:
        repository = SqlAlchemyRatingResultRepository(session)
        result = make_result(fixture, period.id)
        await repository.add(result)
        duplicate = make_result(fixture, period.id)
        with pytest.raises(IntegrityError):
            await repository.add(duplicate)
        await session.rollback()

    restrict_match_id = uuid4()
    now = datetime.now(UTC)
    async with fixture.session_factory() as session:
        session.add(
            MatchModel(
                id=restrict_match_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.COMPLETED,
                created_by=fixture.first_user_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                winner_id=fixture.first_user_id,
                started_at=now,
                completed_at=now,
                version=1,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=restrict_match_id,
                    user_id=fixture.first_user_id,
                    position=1,
                ),
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=restrict_match_id,
                    user_id=fixture.second_user_id,
                    position=2,
                ),
            ]
        )
        restricted_input = replace(
            make_input(fixture, period.id),
            match_id=restrict_match_id,
            correction_revision_id=None,
            result_fingerprint="d" * 64,
        )
        await SqlAlchemyRatingInputRepository(session).add(restricted_input)
        await session.commit()
    async with fixture.session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(delete(MatchModel).where(MatchModel.id == restrict_match_id))
            await session.flush()
        await session.rollback()

    async with fixture.session_factory() as session:
        persisted = await session.scalar(
            select(RatingPeriodMatchInputModel).where(
                RatingPeriodMatchInputModel.match_id == restrict_match_id
            )
        )
        assert persisted is not None
        assert await session.get(UserModel, fixture.first_user_id) is not None


@pytest.mark.parametrize(
    "invalid_rating",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")],
    ids=["zero", "negative", "nan", "infinity"],
)
async def test_rating_requires_a_positive_finite_value(
    rating_database: RatingFixture,
    invalid_rating: Decimal,
) -> None:
    async with rating_database.session_factory() as session:
        session.add(
            RatingModel(
                user_id=rating_database.first_user_id,
                rating=invalid_rating,
                rd=Decimal("350"),
                volatility=Decimal("0.06"),
                rated_matches_count=0,
                version=1,
            )
        )
        with pytest.raises(DBAPIError):
            await session.flush()
        await session.rollback()


async def test_result_check_constraints_reject_invalid_versions_and_counts(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period = make_period(starts_at=datetime(2098, 1, 1, tzinfo=UTC))
    async with fixture.session_factory() as session:
        await SqlAlchemyRatingPeriodRepository(session).add(period)
        await session.commit()

    async with fixture.session_factory() as session:
        result = make_result(fixture, period.id)
        session.add(
            RatingPeriodResultModel(
                id=result.id,
                period_id=result.period_id,
                calculation_version=0,
                user_id=result.user_id,
                rating_before=result.rating_before,
                rating_after=result.rating_after,
                rd_before=result.rd_before,
                rd_after=result.rd_after,
                volatility_before=result.volatility_before,
                volatility_after=result.volatility_after,
                games_count=-1,
                is_active=result.is_active,
                created_at=result.created_at,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_rebuild_repositories_barrier_checkpoints_and_request_link(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    now = datetime.now(UTC)
    periods = tuple(make_period(starts_at=datetime(2100, 1, day, tzinfo=UTC)) for day in (1, 2, 3))
    job = RatingRebuildJob(
        id=uuid4(),
        from_period_id=periods[0].id,
        through_period_id=periods[2].id,
        status=RatingRebuildStatus.PENDING,
        singleton_scope=1,
        created_at=now,
        started_at=None,
        ready_at=None,
        completed_at=None,
        error_code=None,
        version=1,
    )
    request = RatingRecalculationRequest(
        id=uuid4(),
        from_period_id=periods[0].id,
        caused_by_match_id=fixture.match_id,
        caused_by_revision_id=fixture.revision_id,
        rebuild_job_id=None,
        status=RatingRecalculationStatus.PENDING,
        created_at=now,
        started_at=None,
        completed_at=None,
        error_code=None,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        for period in periods:
            await uow.periods.add(period)
        assert await uow.rebuild_jobs.add(job) == job
        await uow.recalculations.add(request)
        for index in (2, 0, 1):
            await uow.rebuild_periods.add(
                RatingRebuildPeriod(
                    rebuild_job_id=job.id,
                    period_id=periods[index].id,
                    source_version=1,
                    target_version=2,
                    status=RatingRebuildPeriodStatus.PENDING,
                    created_at=now,
                    completed_at=None,
                    error_code=None,
                )
            )
        linked = await uow.recalculations.link_to_job(
            request_id=request.id,
            rebuild_job_id=job.id,
            expected_rebuild_job_id=None,
        )
        await uow.commit()

    assert linked is not None and linked.rebuild_job_id == job.id
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        assert await uow.rebuild_jobs.get(job.id, for_update=True) == job
        assert await uow.rebuild_jobs.get_active() == job
        assert await uow.rebuild_jobs.get_publication_barrier() == job
        checkpoints = tuple(await uow.rebuild_periods.list_for_job(job.id))
        linked_requests = tuple(await uow.recalculations.list_for_job(job.id))
    assert tuple(item.period_id for item in checkpoints) == tuple(item.id for item in periods)
    assert linked_requests == (linked,)


async def test_rebuild_constraints_singleton_target_version_and_rollback(
    rating_database: RatingFixture,
) -> None:
    now = datetime.now(UTC)
    first_period = make_period(starts_at=datetime(2101, 1, 1, tzinfo=UTC))
    second_period = make_period(starts_at=datetime(2101, 1, 2, tzinfo=UTC))
    first_job = RatingRebuildJob(
        id=uuid4(),
        from_period_id=first_period.id,
        through_period_id=second_period.id,
        status=RatingRebuildStatus.FAILED,
        singleton_scope=1,
        created_at=now,
        started_at=now,
        ready_at=None,
        completed_at=None,
        error_code="injected_failure",
        version=1,
    )
    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        await uow.periods.add(first_period)
        await uow.periods.add(second_period)
        await uow.rebuild_jobs.add(first_job)
        await uow.rebuild_periods.add(
            RatingRebuildPeriod(
                rebuild_job_id=first_job.id,
                period_id=first_period.id,
                source_version=1,
                target_version=2,
                status=RatingRebuildPeriodStatus.FAILED,
                created_at=now,
                completed_at=now,
                error_code="injected_failure",
            )
        )
        await uow.commit()

    second_active = replace(first_job, id=uuid4(), status=RatingRebuildStatus.PENDING)
    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        assert await uow.rebuild_jobs.get_publication_barrier() == first_job

    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        with pytest.raises(IntegrityError):
            await uow.rebuild_jobs.add(second_active)

    completed_job = replace(
        first_job,
        id=uuid4(),
        status=RatingRebuildStatus.COMPLETED,
        completed_at=now,
        error_code=None,
    )
    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        await uow.rebuild_jobs.add(completed_job)
        with pytest.raises(IntegrityError):
            await uow.rebuild_periods.add(
                RatingRebuildPeriod(
                    rebuild_job_id=completed_job.id,
                    period_id=first_period.id,
                    source_version=1,
                    target_version=2,
                    status=RatingRebuildPeriodStatus.READY,
                    created_at=now,
                    completed_at=now,
                    error_code=None,
                )
            )

    rolled_back_job = replace(
        completed_job,
        id=uuid4(),
        status=RatingRebuildStatus.SUPERSEDED,
    )
    async with SqlAlchemyRatingsUnitOfWork(rating_database.session_factory) as uow:
        await uow.rebuild_jobs.add(rolled_back_job)
    async with rating_database.session_factory() as session:
        assert await session.get(RatingRebuildJobModel, rolled_back_job.id) is None


async def test_published_correction_creates_rebuild_and_persistent_barrier(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period_start = datetime(2103, 1, 1, tzinfo=UTC)
    now = datetime(2103, 1, 3, tzinfo=UTC)
    period = replace(
        make_period(starts_at=period_start),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=now,
    )
    current_rating = PublishedRating(
        user_id=fixture.first_user_id,
        rating=Decimal("1512.34567"),
        rd=Decimal("290.12345"),
        volatility=Decimal("0.0599990000"),
        as_of_period_id=period.id,
        rated_matches_count=1,
        updated_at=now,
        version=1,
    )
    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        assert match is not None
        match.completed_at = period_start + timedelta(hours=12)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert revision_model is not None
        revision_model.operation = MatchRevisionOperation.CORRECTION
        await SqlAlchemyRatingPeriodRepository(session).add(period)
        await SqlAlchemyRatingInputRepository(session).add(
            replace(
                make_input(fixture, period.id),
                completed_at=match.completed_at,
            )
        )
        await SqlAlchemyRatingRepository(session).add(current_rating)
        await session.commit()

    async with fixture.session_factory() as session:
        match_view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match_view is not None and revision_model is not None
        revision = revision_model.to_domain()

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await RatingCorrectionCoordinator(clock=lambda: now).coordinate(
            periods=uow.periods,
            inputs=uow.inputs,
            recalculations=uow.recalculations,
            rebuild_jobs=uow.rebuild_jobs,
            rebuild_periods=uow.rebuild_periods,
            match=match_view,
            revision=revision,
        )
        await uow.commit()

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored_period = await uow.periods.get(period.id)
        job = await uow.rebuild_jobs.get_active()
        stored_rating = await uow.ratings.get(fixture.first_user_id)
        assert stored_period is not None and job is not None
        checkpoint = await uow.rebuild_periods.get(job.id, period.id)
        requests = tuple(await uow.recalculations.list_for_job(job.id))
    assert stored_period.status is RatingPeriodStatus.PUBLISHED
    assert stored_period.published_version == 1
    assert stored_period.calculation_version == 2
    assert stored_period.calculated_version is None
    assert checkpoint is not None
    assert checkpoint.source_version == 1
    assert checkpoint.target_version == 2
    assert len(requests) == 1
    assert requests[0].caused_by_revision_id == fixture.revision_id
    assert stored_rating == current_rating

    later_period = replace(
        make_period(starts_at=period_start + timedelta(days=1)),
        status=RatingPeriodStatus.CALCULATING,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(later_period)
        await uow.commit()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        assert await uow.rebuild_periods.get(job.id, later_period.id) is None

    with pytest.raises(RatingRebuildBarrierError):
        await RatingPeriodPublisher(clock=lambda: now).publish(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )
    with pytest.raises(RatingRebuildBarrierError):
        await RatingPeriodResultPersister(clock=lambda: now).persist(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=2,
        )
    with pytest.raises(RatingRebuildBarrierError):
        await RatingPeriodResultPersister(clock=lambda: now).persist(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=later_period.id,
            expected_calculation_version=1,
        )

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        locked_job = await uow.rebuild_jobs.get(job.id, for_update=True)
        assert locked_job is not None
        failed = await uow.rebuild_jobs.update_status(
            job_id=job.id,
            status=RatingRebuildStatus.FAILED,
            expected_version=locked_job.version,
            completed_at=now,
            error_code="injected_failure",
        )
        assert failed is not None
        await uow.commit()

    with pytest.raises(RatingRebuildBarrierError):
        await RatingPeriodPublisher(clock=lambda: now).publish(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )

    second_revision_id = uuid4()
    async with fixture.session_factory() as session:
        session.add(
            MatchResultRevisionModel(
                id=second_revision_id,
                match_id=fixture.match_id,
                revision_number=2,
                changed_by=fixture.first_user_id,
                previous_result={"winner_id": str(fixture.first_user_id), "sets": []},
                new_result={"winner_id": str(fixture.second_user_id), "sets": []},
                reason="supersede failed rebuild",
                operation=MatchRevisionOperation.CORRECTION,
            )
        )
        await session.commit()
    async with fixture.session_factory() as session:
        match_view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        second_revision_model = await session.get(MatchResultRevisionModel, second_revision_id)
        assert match_view is not None and second_revision_model is not None
        second_revision = second_revision_model.to_domain()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await RatingCorrectionCoordinator(clock=lambda: now + timedelta(minutes=1)).coordinate(
            periods=uow.periods,
            inputs=uow.inputs,
            recalculations=uow.recalculations,
            rebuild_jobs=uow.rebuild_jobs,
            rebuild_periods=uow.rebuild_periods,
            match=match_view,
            revision=second_revision,
        )
        await uow.commit()

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        superseded = await uow.rebuild_jobs.get(job.id)
        replacement = await uow.rebuild_jobs.get_active()
        stored_period = await uow.periods.get(period.id)
        assert superseded is not None and replacement is not None and stored_period is not None
        replacement_checkpoint = await uow.rebuild_periods.get(replacement.id, period.id)
        replacement_requests = tuple(await uow.recalculations.list_for_job(replacement.id))
    assert superseded.status is RatingRebuildStatus.SUPERSEDED
    assert replacement.id != job.id
    assert replacement_checkpoint is not None and replacement_checkpoint.target_version == 3
    assert stored_period.calculation_version == 3
    assert stored_period.published_version == 1
    assert {item.caused_by_revision_id for item in replacement_requests} == {
        fixture.revision_id,
        second_revision_id,
    }

    with pytest.raises(RatingRebuildBarrierError):
        await RatingPeriodPublisher(clock=lambda: now).publish(
            SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            period_id=period.id,
            expected_calculation_version=1,
        )


async def test_shadow_rebuild_calculates_multiple_periods_without_cutover(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    previous_start = datetime(2110, 1, 1, tzinfo=UTC)
    first_start = datetime(2110, 1, 5, tzinfo=UTC)
    second_start = datetime(2110, 1, 10, tzinfo=UTC)
    now = datetime(2110, 1, 12, tzinfo=UTC)
    previous = replace(
        make_period(starts_at=previous_start, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=now,
    )
    first = replace(
        make_period(starts_at=first_start, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=now,
    )
    second = replace(
        make_period(starts_at=second_start, algorithm_version="2"),
        status=RatingPeriodStatus.CALCULATING,
        calculated_version=1,
    )
    second_match_id = uuid4()
    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        revision = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match is not None and revision is not None
        match.completed_at = first_start + timedelta(hours=12)
        revision.operation = MatchRevisionOperation.CORRECTION
        session.add(
            MatchModel(
                id=second_match_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.COMPLETED,
                created_by=fixture.first_user_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                winner_id=fixture.second_user_id,
                started_at=second_start + timedelta(hours=11),
                completed_at=second_start + timedelta(hours=12),
                version=2,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=second_match_id,
                    user_id=fixture.first_user_id,
                    position=1,
                ),
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=second_match_id,
                    user_id=fixture.second_user_id,
                    position=2,
                ),
            ]
        )
        session.add(
            MatchSetModel(
                id=uuid4(),
                match_id=second_match_id,
                set_number=1,
                player1_score=4,
                player2_score=11,
            )
        )
        await session.commit()

    previous_results = (
        replace(make_result(fixture, previous.id), rd_after=Decimal("80.00000")),
        replace(
            make_result(fixture, previous.id),
            id=uuid4(),
            user_id=fixture.second_user_id,
            rd_after=Decimal("90.00000"),
        ),
    )
    first_source = replace(
        make_input(fixture, first.id),
        completed_at=first_start + timedelta(hours=12),
    )
    second_source = replace(
        make_input(fixture, second.id),
        match_id=second_match_id,
        correction_revision_id=None,
        result_fingerprint="b" * 64,
        score1=Decimal("0.00"),
        score2=Decimal("1.00"),
        completed_at=second_start + timedelta(hours=12),
    )
    initial_ratings = (
        PublishedRating(
            user_id=fixture.first_user_id,
            rating=Decimal("1500.00000"),
            rd=Decimal("80.00000"),
            volatility=Decimal("0.0600000000"),
            as_of_period_id=previous.id,
            rated_matches_count=1,
            updated_at=now,
            version=1,
        ),
        PublishedRating(
            user_id=fixture.second_user_id,
            rating=Decimal("1500.00000"),
            rd=Decimal("90.00000"),
            volatility=Decimal("0.0600000000"),
            as_of_period_id=previous.id,
            rated_matches_count=1,
            updated_at=now,
            version=1,
        ),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        for period in (previous, first, second):
            await uow.periods.add(period)
        await uow.results.add_all(previous_results)
        await uow.inputs.add(first_source)
        await uow.inputs.add(second_source)
        for rating in initial_ratings:
            await uow.ratings.add(rating)
        await uow.commit()

    async with fixture.session_factory() as session:
        match_view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match_view is not None and revision_model is not None
        revision = revision_model.to_domain()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await RatingCorrectionCoordinator(clock=lambda: now).coordinate(
            periods=uow.periods,
            inputs=uow.inputs,
            recalculations=uow.recalculations,
            rebuild_jobs=uow.rebuild_jobs,
            rebuild_periods=uow.rebuild_periods,
            match=match_view,
            revision=revision,
        )
        job = await uow.rebuild_jobs.get_active()
        assert job is not None
        await uow.commit()

    ready = await RatingRebuildExecutor(clock=lambda: now).run(
        lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        job_id=job.id,
    )
    repeated = await RatingRebuildExecutor(clock=lambda: now).run(
        lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        job_id=job.id,
    )
    assert ready.status is repeated.status is RatingRebuildStatus.READY

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored_first = await uow.periods.get(first.id)
        stored_second = await uow.periods.get(second.id)
        checkpoints = tuple(await uow.rebuild_periods.list_for_job(job.id))
        first_shadow_inputs = tuple(await uow.inputs.list_for_period(first.id, 2))
        first_shadow_results = tuple(await uow.results.list_for_period(first.id, 2))
        second_shadow_results = tuple(await uow.results.list_for_period(second.id, 2))
        stored_ratings = (
            await uow.ratings.get(fixture.first_user_id),
            await uow.ratings.get(fixture.second_user_id),
        )
    assert stored_first is not None and stored_second is not None
    assert stored_first.published_version == 1
    assert stored_second.published_version is None
    assert stored_first.published_at == now
    assert stored_second.published_at is None
    assert stored_first.calculated_version == stored_second.calculated_version == 2
    assert all(item.status is RatingRebuildPeriodStatus.READY for item in checkpoints)
    assert first_shadow_inputs[0].correction_revision_id == fixture.revision_id
    assert len(first_shadow_results) == len(second_shadow_results) == 2
    assert second_shadow_results[0].rating_before == first_shadow_results[0].rating_after
    assert stored_ratings == initial_ratings

    cutover_at = now + timedelta(hours=1)
    completed = await RatingRebuildCutover(clock=lambda: cutover_at).activate(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory), job_id=job.id
    )
    repeated_cutover = await RatingRebuildCutover(clock=lambda: cutover_at).activate(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory), job_id=job.id
    )

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        activated_first = await uow.periods.get(first.id)
        activated_second = await uow.periods.get(second.id)
        current = tuple(await uow.ratings.list_all())
        requests = tuple(await uow.recalculations.list_for_job(job.id))
        barrier = await uow.rebuild_jobs.get_publication_barrier()
    assert completed.status is repeated_cutover.status is RatingRebuildStatus.COMPLETED
    assert activated_first is not None and activated_second is not None
    assert activated_first.published_version == activated_second.published_version == 2
    assert activated_first.published_at == now
    assert activated_second.published_at == cutover_at
    assert {item.rated_matches_count for item in current} == {2}
    assert {item.as_of_period_id for item in current} == {second.id}
    assert requests and all(item.status is RatingRecalculationStatus.COMPLETED for item in requests)
    assert barrier is None


async def test_shadow_rebuild_void_creates_empty_branch_and_preserves_source(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    starts_at = datetime(2111, 1, 1, tzinfo=UTC)
    now = datetime(2111, 1, 3, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=now,
    )
    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match is not None and revision_model is not None
        match.status = MatchStatus.VOIDED
        match.winner_id = None
        match.completed_at = starts_at + timedelta(hours=12)
        match.version = 3
        revision_model.operation = MatchRevisionOperation.VOID
        await session.commit()
    source = replace(
        make_input(fixture, period.id),
        completed_at=starts_at + timedelta(hours=12),
    )
    source_results = (
        make_result(fixture, period.id),
        replace(
            make_result(fixture, period.id),
            id=uuid4(),
            user_id=fixture.second_user_id,
        ),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(source)
        await uow.results.add_all(source_results)
        for result in source_results:
            await uow.ratings.add(
                PublishedRating(
                    user_id=result.user_id,
                    rating=result.rating_after,
                    rd=result.rd_after,
                    volatility=result.volatility_after,
                    as_of_period_id=period.id,
                    rated_matches_count=1,
                    updated_at=now,
                    version=1,
                )
            )
        await uow.commit()
    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert view is not None and revision_model is not None
        revision = revision_model.to_domain()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await RatingCorrectionCoordinator(clock=lambda: now).coordinate(
            periods=uow.periods,
            inputs=uow.inputs,
            recalculations=uow.recalculations,
            rebuild_jobs=uow.rebuild_jobs,
            rebuild_periods=uow.rebuild_periods,
            match=view,
            revision=revision,
        )
        job = await uow.rebuild_jobs.get_active()
        assert job is not None
        await uow.commit()

    await RatingRebuildExecutor(clock=lambda: now).run(
        lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        job_id=job.id,
    )

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored = await uow.periods.get(period.id)
        checkpoint = await uow.rebuild_periods.get(job.id, period.id)
        source_inputs = tuple(await uow.inputs.list_for_period(period.id, 1))
        shadow_inputs = tuple(await uow.inputs.list_for_period(period.id, 2))
        shadow_results = tuple(await uow.results.list_for_period(period.id, 2))
    assert stored is not None and stored.published_version == 1
    assert stored.calculated_version == 2
    assert checkpoint is not None and checkpoint.status is RatingRebuildPeriodStatus.READY
    assert source_inputs == (source,)
    assert shadow_inputs == shadow_results == ()

    with pytest.raises(RuntimeError, match="injected cutover failure"):
        await RatingRebuildCutover(clock=lambda: now + timedelta(hours=1)).activate(
            FailingCutoverUow(fixture.session_factory), job_id=job.id
        )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        rolled_back_period = await uow.periods.get(period.id)
        rolled_back_job = await uow.rebuild_jobs.get(job.id)
        rolled_back_ratings = tuple(await uow.ratings.list_all())
        barrier = await uow.rebuild_jobs.get_publication_barrier()
    assert rolled_back_period is not None and rolled_back_period.published_version == 1
    assert rolled_back_job is not None and rolled_back_job.status is RatingRebuildStatus.READY
    assert len(rolled_back_ratings) == 2
    assert barrier is not None and barrier.id == job.id

    await RatingRebuildCutover(clock=lambda: now + timedelta(hours=1)).activate(
        SqlAlchemyRatingsUnitOfWork(fixture.session_factory), job_id=job.id
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        activated = await uow.periods.get(period.id)
        ratings = tuple(await uow.ratings.list_all())
        barrier = await uow.rebuild_jobs.get_publication_barrier()
    assert activated is not None and activated.published_version == 2
    assert ratings == ()
    assert barrier is None


async def test_scheduler_recovers_pending_rebuild_and_resumes_normal_workflow(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    starts_at = datetime(2114, 1, 1, tzinfo=UTC)
    now = datetime(2114, 1, 3, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=None,
        published_version=1,
        published_at=now,
    )
    job = RatingRebuildJob(
        id=uuid4(),
        from_period_id=period.id,
        through_period_id=period.id,
        status=RatingRebuildStatus.PENDING,
        singleton_scope=1,
        created_at=now,
        started_at=None,
        ready_at=None,
        completed_at=None,
        error_code=None,
        version=1,
    )
    checkpoint = RatingRebuildPeriod(
        rebuild_job_id=job.id,
        period_id=period.id,
        source_version=1,
        target_version=2,
        status=RatingRebuildPeriodStatus.PENDING,
        created_at=now,
        completed_at=None,
        error_code=None,
    )
    request = RatingRecalculationRequest(
        id=uuid4(),
        from_period_id=period.id,
        caused_by_match_id=fixture.match_id,
        caused_by_revision_id=fixture.revision_id,
        rebuild_job_id=job.id,
        status=RatingRecalculationStatus.PENDING,
        created_at=now,
        started_at=None,
        completed_at=None,
        error_code=None,
    )
    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        revision = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match is not None and revision is not None
        match.started_at = starts_at + timedelta(hours=11)
        match.completed_at = starts_at + timedelta(hours=12)
        revision.operation = MatchRevisionOperation.CORRECTION
        await session.commit()
    source = replace(
        make_input(fixture, period.id),
        completed_at=starts_at + timedelta(hours=12),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(source)
        await uow.rebuild_jobs.add(job)
        await uow.rebuild_periods.add(checkpoint)
        await uow.recalculations.add(request)
        for user_id in (fixture.first_user_id, fixture.second_user_id):
            await uow.ratings.add(
                PublishedRating(
                    user_id=user_id,
                    rating=Decimal("1500.00000"),
                    rd=Decimal("350.00000"),
                    volatility=Decimal("0.0600000000"),
                    as_of_period_id=period.id,
                    rated_matches_count=1,
                    updated_at=now,
                    version=1,
                )
            )
        await uow.commit()

    normal_calls = 0

    class NormalWorkflow:
        async def run(self, uow_factory):
            nonlocal normal_calls
            del uow_factory
            normal_calls += 1
            return ()

    scheduler = RatingPeriodScheduler(
        lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        rebuild=RatingRebuildOrchestrator(clock=lambda: now + timedelta(hours=1)),
        workflow=NormalWorkflow(),
        clock=lambda: now + timedelta(hours=1),
    )

    await scheduler.run_cycle()

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored_job = await uow.rebuild_jobs.get(job.id)
        stored_period = await uow.periods.get(period.id)
        stored_checkpoint = await uow.rebuild_periods.get(job.id, period.id)
        stored_request = await uow.recalculations.get_by_revision(fixture.revision_id)
        barrier = await uow.rebuild_jobs.get_publication_barrier()
    assert stored_job is not None and stored_job.status is RatingRebuildStatus.COMPLETED
    assert stored_period is not None and stored_period.published_version == 2
    assert stored_checkpoint is not None
    assert stored_checkpoint.status is RatingRebuildPeriodStatus.READY
    assert stored_request is not None
    assert stored_request.status is RatingRecalculationStatus.COMPLETED
    assert barrier is None
    assert normal_calls == 1


async def test_shadow_period_failure_rolls_back_inputs_results_and_checkpoint(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    starts_at = datetime(2112, 1, 1, tzinfo=UTC)
    now = datetime(2112, 1, 3, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, algorithm_version="unsupported"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=now,
    )
    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert match is not None and revision_model is not None
        match.completed_at = starts_at + timedelta(hours=12)
        revision_model.operation = MatchRevisionOperation.CORRECTION
        await session.commit()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(
            replace(
                make_input(fixture, period.id),
                completed_at=starts_at + timedelta(hours=12),
            )
        )
        await uow.commit()
    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        revision_model = await session.get(MatchResultRevisionModel, fixture.revision_id)
        assert view is not None and revision_model is not None
        revision = revision_model.to_domain()
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await RatingCorrectionCoordinator(clock=lambda: now).coordinate(
            periods=uow.periods,
            inputs=uow.inputs,
            recalculations=uow.recalculations,
            rebuild_jobs=uow.rebuild_jobs,
            rebuild_periods=uow.rebuild_periods,
            match=view,
            revision=revision,
        )
        job = await uow.rebuild_jobs.get_active()
        assert job is not None
        await uow.commit()

    with pytest.raises(UnsupportedRatingAlgorithmError):
        await RatingRebuildExecutor(clock=lambda: now).run(
            lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            job_id=job.id,
        )

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        stored = await uow.periods.get(period.id)
        stored_job = await uow.rebuild_jobs.get(job.id)
        checkpoint = await uow.rebuild_periods.get(job.id, period.id)
        shadow_inputs = tuple(await uow.inputs.list_for_period(period.id, 2))
        shadow_results = tuple(await uow.results.list_for_period(period.id, 2))
    assert stored is not None and stored.calculated_version is None
    assert stored_job is not None and stored_job.status is RatingRebuildStatus.PREPARING
    assert checkpoint is not None and checkpoint.status is RatingRebuildPeriodStatus.PENDING
    assert shadow_inputs == shadow_results == ()


async def test_rating_queries_expose_only_active_versions_with_stable_pagination(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    starts = tuple(datetime(2114, 1, day, tzinfo=UTC) for day in (1, 2, 3))
    periods = tuple(
        replace(
            make_period(starts_at=start, algorithm_version="2"),
            status=RatingPeriodStatus.PUBLISHED,
            calculation_version=2,
            calculated_version=2,
            published_version=1,
            published_at=start + timedelta(days=1, minutes=5),
        )
        for start in starts
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        for index, period in enumerate(periods):
            await uow.periods.add(period)
            active = replace(
                make_result(fixture, period.id),
                rating_after=Decimal(1510 + index),
            )
            shadow = replace(
                active,
                id=uuid4(),
                calculation_version=2,
                rating_after=Decimal(1710 + index),
            )
            await uow.results.add_all((active, shadow))
        await uow.ratings.add(
            PublishedRating(
                user_id=fixture.first_user_id,
                rating=Decimal("1512.00000"),
                rd=Decimal("290.00000"),
                volatility=Decimal("0.0600000000"),
                as_of_period_id=periods[-1].id,
                rated_matches_count=3,
                updated_at=periods[-1].ends_at,
                version=1,
            )
        )
        await uow.commit()

    async with fixture.session_factory() as session:
        queries = SqlAlchemyRatingQueryRepository(session)
        current = await queries.get_current(fixture.first_user_id)
        first_page, total = await queries.list_history(fixture.first_user_id, limit=2, offset=0)
        second_page, repeated_total = await queries.list_history(
            fixture.first_user_id, limit=2, offset=2
        )
        filtered_page, filtered_total = await queries.list_history(
            fixture.first_user_id,
            limit=1,
            offset=1,
            from_date=date(2114, 1, 2),
            to_date=date(2114, 1, 3),
        )

    assert current is not None and current[1].id == periods[-1].id
    assert total == repeated_total == 3
    assert [period.id for _, period in first_page] == [periods[2].id, periods[1].id]
    assert [period.id for _, period in second_page] == [periods[0].id]
    assert filtered_total == 2
    assert [period.id for _, period in filtered_page] == [periods[1].id]
    assert {result.calculation_version for result, _ in first_page + second_page} == {1}
    assert {result.rating_after for result, _ in first_page + second_page} == {
        Decimal("1510.00000"),
        Decimal("1511.00000"),
        Decimal("1512.00000"),
    }


async def test_rating_status_tracks_pending_publication_and_personal_rebuild(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    unrelated_user_id = uuid4()
    period_start = datetime(2114, 2, 1, tzinfo=UTC)
    before_close = period_start + timedelta(hours=18)
    after_close = period_start + timedelta(days=1, minutes=6)
    async with fixture.session_factory() as session:
        await SqlAlchemyUserRepository(session).create(make_user(unrelated_user_id, "Unrelated"))
        match = await session.get(MatchModel, fixture.match_id)
        assert match is not None
        match.started_at = period_start + timedelta(hours=11)
        match.completed_at = period_start + timedelta(hours=12)
        await add_historical_match(
            session,
            match_id=uuid4(),
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=period_start + timedelta(hours=13),
            is_ranked=False,
        )
        await add_historical_match(
            session,
            match_id=uuid4(),
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=period_start + timedelta(hours=14),
            status=MatchStatus.VOIDED,
        )
        cancelled_match_id = uuid4()
        session.add(
            MatchModel(
                id=cancelled_match_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.CANCELLED,
                created_by=fixture.first_user_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                winner_id=None,
                started_at=None,
                completed_at=None,
                cancelled_at=period_start + timedelta(hours=15),
                version=1,
            )
        )
        session.add_all(
            (
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=cancelled_match_id,
                    user_id=fixture.first_user_id,
                    position=1,
                ),
                MatchParticipantModel(
                    id=uuid4(),
                    match_id=cancelled_match_id,
                    user_id=fixture.second_user_id,
                    position=2,
                ),
            )
        )
        await session.commit()

    async with fixture.session_factory() as session:
        repository = SqlAlchemyRatingQueryRepository(session)
        pending = await RatingStatusService(clock=lambda: before_close).get_status(
            repository, user_id=fixture.first_user_id
        )
        unrated = await RatingStatusService(clock=lambda: before_close).get_status(
            repository, user_id=unrelated_user_id
        )
    assert pending.state is RatingUpdateState.PENDING
    assert pending.pending_matches_count == 1
    assert unrated.state is RatingUpdateState.UP_TO_DATE
    assert unrated.pending_matches_count == 0

    async with fixture.session_factory() as session:
        overdue = await RatingStatusService(clock=lambda: after_close).get_status(
            SqlAlchemyRatingQueryRepository(session), user_id=fixture.first_user_id
        )
    assert overdue.state is RatingUpdateState.DELAYED
    assert overdue.pending_matches_count == 1

    period = replace(
        make_period(starts_at=period_start, algorithm_version="2"),
        status=RatingPeriodStatus.CALCULATING,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(
            replace(make_input(fixture, period.id), completed_at=period_start + timedelta(hours=12))
        )
        await uow.commit()

    async with fixture.session_factory() as session:
        calculating = await RatingStatusService(clock=lambda: after_close).get_status(
            SqlAlchemyRatingQueryRepository(session), user_id=fixture.first_user_id
        )
    assert calculating.state is RatingUpdateState.CALCULATING
    assert calculating.pending_matches_count == 1

    async with fixture.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        assert stored_period is not None
        stored_period.status = RatingPeriodStatus.PUBLISHED
        stored_period.calculated_version = 1
        stored_period.published_version = 1
        stored_period.published_at = after_close
        await SqlAlchemyRatingResultRepository(session).add(make_result(fixture, period.id))
        await SqlAlchemyRatingRepository(session).add(
            PublishedRating(
                user_id=fixture.first_user_id,
                rating=Decimal("1512.34567"),
                rd=Decimal("290.12345"),
                volatility=Decimal("0.0599990000"),
                as_of_period_id=period.id,
                rated_matches_count=1,
                updated_at=after_close,
                version=1,
            )
        )
        await session.commit()

    async with fixture.session_factory() as session:
        published = await RatingStatusService(clock=lambda: after_close).get_status(
            SqlAlchemyRatingQueryRepository(session), user_id=fixture.first_user_id
        )
    assert published.state is RatingUpdateState.UP_TO_DATE
    assert published.pending_matches_count == 0

    # A prepared shadow input is not active accounting and must not duplicate a
    # match that is already represented by the published version.
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.inputs.add(
            replace(
                make_input(fixture, period.id, calculation_version=2),
                completed_at=period_start + timedelta(hours=12),
            )
        )
        await uow.commit()

    job = RatingRebuildJob(
        id=uuid4(),
        from_period_id=period.id,
        through_period_id=period.id,
        status=RatingRebuildStatus.PENDING,
        singleton_scope=1,
        created_at=after_close,
        started_at=None,
        ready_at=None,
        completed_at=None,
        error_code=None,
        version=1,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.rebuild_jobs.add(job)
        await uow.rebuild_periods.add(
            RatingRebuildPeriod(
                rebuild_job_id=job.id,
                period_id=period.id,
                source_version=1,
                target_version=2,
                status=RatingRebuildPeriodStatus.PENDING,
                created_at=after_close,
                completed_at=None,
                error_code=None,
            )
        )
        await uow.commit()

    async with fixture.session_factory() as session:
        repository = SqlAlchemyRatingQueryRepository(session)
        rebuilding = await RatingStatusService(clock=lambda: after_close).get_status(
            repository, user_id=fixture.first_user_id
        )
        unrelated = await RatingStatusService(clock=lambda: after_close).get_status(
            repository, user_id=unrelated_user_id
        )
    assert rebuilding.state is RatingUpdateState.REBUILDING
    assert rebuilding.pending_matches_count == 0
    assert unrelated.state is RatingUpdateState.UP_TO_DATE

    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        failed = await uow.rebuild_jobs.update_status(
            job_id=job.id,
            status=RatingRebuildStatus.FAILED,
            expected_version=1,
            completed_at=None,
            error_code="test_failure",
        )
        assert failed is not None
        await uow.commit()
    async with fixture.session_factory() as session:
        delayed = await RatingStatusService(clock=lambda: after_close).get_status(
            SqlAlchemyRatingQueryRepository(session), user_id=fixture.first_user_id
        )
    assert delayed.state is RatingUpdateState.DELAYED
    assert delayed.pending_matches_count == 0


async def test_match_rating_state_follows_published_version_shadow_and_void(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    state_service = RatingMatchStateService()

    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        assert view is not None
        pending = await state_service.get_states(
            SqlAlchemyRatingQueryRepository(session),
            (view.match,),
        )
    assert pending[fixture.match_id] is MatchRatingState.PENDING

    period_start = datetime(2114, 3, 1, tzinfo=UTC)
    period = replace(
        make_period(starts_at=period_start, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=2,
        published_version=1,
        published_at=period_start + timedelta(days=1, minutes=5),
    )
    active_input = replace(
        make_input(fixture, period.id),
        completed_at=period_start + timedelta(hours=12),
    )
    shadow_input = replace(
        active_input,
        calculation_version=2,
        source_match_version=3,
        result_fingerprint="b" * 64,
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        await uow.inputs.add(active_input)
        await uow.inputs.add(shadow_input)
        await uow.commit()

    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        assert view is not None
        rated = await state_service.get_states(
            SqlAlchemyRatingQueryRepository(session),
            (view.match,),
        )
    assert rated[fixture.match_id] is MatchRatingState.RATED

    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        assert match is not None
        match.version = 3
        await session.commit()
    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        assert view is not None
        recalculating = await state_service.get_states(
            SqlAlchemyRatingQueryRepository(session),
            (view.match,),
        )
    assert recalculating[fixture.match_id] is MatchRatingState.RECALCULATING

    async with fixture.session_factory() as session:
        stored_period = await session.get(RatingPeriodModel, period.id)
        assert stored_period is not None
        stored_period.published_version = 2
        await session.commit()
    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        assert view is not None
        after_cutover = await state_service.get_states(
            SqlAlchemyRatingQueryRepository(session),
            (view.match,),
        )
    assert after_cutover[fixture.match_id] is MatchRatingState.RATED

    async with fixture.session_factory() as session:
        match = await session.get(MatchModel, fixture.match_id)
        assert match is not None
        match.status = MatchStatus.VOIDED
        match.winner_id = None
        match.version = 4
        await session.commit()
    async with fixture.session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(fixture.match_id)
        assert view is not None
        voided = await state_service.get_states(
            SqlAlchemyRatingQueryRepository(session),
            (view.match,),
        )
    assert voided[fixture.match_id] is MatchRatingState.NOT_APPLICABLE


async def test_rating_query_positions_match_leaderboard_order_and_tiebreakers(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    third_user_id, fourth_user_id, fifth_user_id = uuid4(), uuid4(), uuid4()
    async with fixture.session_factory() as session:
        users = SqlAlchemyUserRepository(session)
        await users.create(make_user(third_user_id, "Third"))
        await users.create(make_user(fourth_user_id, "Fourth"))
        await users.create(make_user(fifth_user_id, "Fifth"))
        await session.commit()

    async with fixture.session_factory() as session:
        empty, total, current_position = await SqlAlchemyLeaderboardRepository(session).list_ranked(
            current_user_id=fixture.first_user_id,
            limit=20,
            offset=0,
        )
    assert empty == ()
    assert total == 0
    assert current_position is None

    starts_at = datetime(2115, 1, 1, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=starts_at + timedelta(days=1, minutes=5),
    )
    rating_specs = (
        (fifth_user_id, "1700.00000", "200.00000", 1),
        (fixture.first_user_id, "1600.00000", "80.00000", 5),
        (fixture.second_user_id, "1600.00000", "70.00000", 3),
        (third_user_id, "1600.00000", "70.00000", 5),
        (fourth_user_id, "1600.00000", "70.00000", 5),
    )
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(period)
        for user_id, rating, rd, match_count in rating_specs:
            await uow.ratings.add(
                PublishedRating(
                    user_id=user_id,
                    rating=Decimal(rating),
                    rd=Decimal(rd),
                    volatility=Decimal("0.0600000000"),
                    as_of_period_id=period.id,
                    rated_matches_count=match_count,
                    updated_at=period.ends_at,
                    version=1,
                )
            )
        await uow.commit()

    tied = sorted((third_user_id, fourth_user_id), key=lambda value: value.int)
    expected = (fifth_user_id, *tied, fixture.second_user_id, fixture.first_user_id)
    async with fixture.session_factory() as session:
        leaderboard = SqlAlchemyLeaderboardRepository(session)
        page, total, current_position = await leaderboard.list_ranked(
            current_user_id=fixture.first_user_id, limit=2, offset=1
        )
        rating_queries = SqlAlchemyRatingQueryRepository(session)
        rating_positions: dict[UUID, int] = {}
        for user_id in expected:
            current = await rating_queries.get_current(user_id)
            assert current is not None
            rating_positions[user_id] = current[2]
    assert total == 5
    assert current_position == 5
    assert [row.position for row in page] == [2, 3]
    assert [row.user.id for row in page] == list(expected[1:3])
    assert rating_positions == {user_id: position for position, user_id in enumerate(expected, 1)}

    async with fixture.session_factory() as session:
        _, _, unrated_position = await SqlAlchemyLeaderboardRepository(session).list_ranked(
            current_user_id=uuid4(),
            limit=1,
            offset=0,
        )
    assert unrated_position is None


async def test_historical_backfill_dry_run_apply_resume_filters_and_calendar_gaps(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    lower = datetime(1888, 1, 1, tzinfo=UTC)
    upper = datetime(1888, 1, 20, tzinfo=UTC)
    second_ranked_id, unranked_id, voided_id = uuid4(), uuid4(), uuid4()
    async with fixture.session_factory() as session:
        original = await session.get(MatchModel, fixture.match_id)
        assert original is not None
        original.started_at = lower + timedelta(hours=11)
        original.completed_at = lower + timedelta(hours=12)
        await add_historical_match(
            session,
            match_id=second_ranked_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=lower + timedelta(days=9, hours=12),
        )
        await add_historical_match(
            session,
            match_id=unranked_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=lower + timedelta(days=10, hours=12),
            is_ranked=False,
        )
        await add_historical_match(
            session,
            match_id=voided_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=lower + timedelta(days=11, hours=12),
            status=MatchStatus.VOIDED,
        )
        await session.commit()

    def factory() -> SqlAlchemyRatingsUnitOfWork:
        return SqlAlchemyRatingsUnitOfWork(fixture.session_factory)

    backfill = RatingHistoricalBackfill(
        clock=lambda: upper,
        batch_size=2,
        completed_from=lower,
        completed_before=upper,
    )
    dry_run = await backfill.run(factory, dry_run=True)
    async with fixture.session_factory() as session:
        period_count_before = await session.scalar(
            select(func.count())
            .select_from(RatingPeriodModel)
            .where(
                RatingPeriodModel.starts_at >= lower,
                RatingPeriodModel.starts_at < upper,
            )
        )

    applied = await backfill.run(factory, dry_run=False)
    repeated = await backfill.run(factory, dry_run=False)

    async with fixture.session_factory() as session:
        periods = (
            await session.scalars(
                select(RatingPeriodModel)
                .where(
                    RatingPeriodModel.starts_at >= lower,
                    RatingPeriodModel.starts_at < upper,
                )
                .order_by(RatingPeriodModel.starts_at)
            )
        ).all()
        inputs = (
            await session.scalars(
                select(RatingPeriodMatchInputModel)
                .where(RatingPeriodMatchInputModel.period_id.in_([item.id for item in periods]))
                .order_by(RatingPeriodMatchInputModel.completed_at)
            )
        ).all()
        ratings = (
            await session.scalars(
                select(RatingModel).where(
                    RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id))
                )
            )
        ).all()

    assert period_count_before == 0
    assert dry_run.scanned_matches == 4
    assert dry_run.eligible_matches == dry_run.processed_matches == 2
    assert dry_run.skipped_matches == 2
    assert dry_run.periods == dry_run.created_periods == 2
    assert applied.processed_matches == applied.published_periods == 2
    assert repeated.processed_matches == repeated.published_periods == 0
    assert repeated.skipped_matches == 4
    assert [item.starts_at.day for item in periods] == [1, 10]
    assert all(item.status is RatingPeriodStatus.PUBLISHED for item in periods)
    assert len(inputs) == 2
    assert inputs[0].correction_revision_id == fixture.revision_id
    assert {item.rated_matches_count for item in ratings} == {2}


async def test_historical_backfill_keeps_current_utc_period_open(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    period_start = datetime(1892, 1, 1, tzinfo=UTC)
    now = period_start + timedelta(hours=18)
    match_id = uuid4()
    async with fixture.session_factory() as session:
        await add_historical_match(
            session,
            match_id=match_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=period_start + timedelta(hours=12),
        )
        await session.commit()

    backfill = RatingHistoricalBackfill(
        clock=lambda: now,
        completed_from=period_start,
        completed_before=period_start + timedelta(days=1),
    )
    report = await backfill.run(
        lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
        dry_run=False,
    )

    async with fixture.session_factory() as session:
        period = await session.scalar(
            select(RatingPeriodModel).where(RatingPeriodModel.starts_at == period_start)
        )
        assert period is not None
        input_count = await session.scalar(
            select(func.count())
            .select_from(RatingPeriodMatchInputModel)
            .where(RatingPeriodMatchInputModel.period_id == period.id)
        )
        result_count = await session.scalar(
            select(func.count())
            .select_from(RatingPeriodResultModel)
            .where(RatingPeriodResultModel.period_id == period.id)
        )
        current_count = await session.scalar(
            select(func.count())
            .select_from(RatingModel)
            .where(RatingModel.user_id.in_((fixture.first_user_id, fixture.second_user_id)))
        )

    assert report.processed_matches == 1
    assert report.published_periods == 0
    assert period.status is RatingPeriodStatus.OPEN
    assert period.calculated_version is None
    assert period.published_version is None
    assert input_count == 1
    assert result_count == 0
    assert current_count == 0


async def test_historical_backfill_conflict_rolls_back_earlier_safe_intake(
    rating_database: RatingFixture,
) -> None:
    fixture = rating_database
    lower = datetime(1889, 1, 1, tzinfo=UTC)
    upper = datetime(1889, 1, 10, tzinfo=UTC)
    safe_match_id, conflict_match_id = uuid4(), uuid4()
    async with fixture.session_factory() as session:
        await add_historical_match(
            session,
            match_id=safe_match_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=lower + timedelta(days=1, hours=12),
        )
        await add_historical_match(
            session,
            match_id=conflict_match_id,
            first_user_id=fixture.first_user_id,
            second_user_id=fixture.second_user_id,
            completed_at=lower + timedelta(days=2, hours=12),
        )
        await session.commit()

    base = replace(
        make_period(starts_at=lower, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=lower + timedelta(days=1, minutes=5),
    )
    conflict_period = make_period(starts_at=lower + timedelta(days=2), algorithm_version="2")
    async with SqlAlchemyRatingsUnitOfWork(fixture.session_factory) as uow:
        await uow.periods.add(base)
        await uow.periods.add(conflict_period)
        await uow.inputs.add(
            RatingPeriodMatchInput(
                period_id=conflict_period.id,
                calculation_version=1,
                match_id=conflict_match_id,
                source_match_version=2,
                correction_revision_id=None,
                result_fingerprint="f" * 64,
                player1_id=fixture.first_user_id,
                player2_id=fixture.second_user_id,
                score1=Decimal("1.00"),
                score2=Decimal("0.00"),
                completed_at=lower + timedelta(days=2, hours=12),
                created_at=upper,
            )
        )
        await uow.commit()

    backfill = RatingHistoricalBackfill(
        clock=lambda: upper,
        completed_from=lower + timedelta(days=1),
        completed_before=upper,
    )
    with pytest.raises(RatingBackfillConflictError) as raised:
        await backfill.run(
            lambda: SqlAlchemyRatingsUnitOfWork(fixture.session_factory),
            dry_run=False,
        )

    async with fixture.session_factory() as session:
        rolled_back_period = await session.scalar(
            select(RatingPeriodModel).where(
                RatingPeriodModel.starts_at == lower + timedelta(days=1)
            )
        )
        stored_conflict = await session.get(
            RatingPeriodMatchInputModel,
            (conflict_period.id, 1, conflict_match_id),
        )
    assert raised.value.report.processed_matches == 1
    assert raised.value.report.conflicted_matches == 1
    assert rolled_back_period is None
    assert stored_conflict is not None


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_concurrent_historical_backfill_counts_match_once() -> None:
    database = Database.from_settings(Settings())
    first_user_id, second_user_id, match_id = uuid4(), uuid4(), uuid4()
    lower = datetime(1891, 1, 1, tzinfo=UTC)
    upper = datetime(1891, 1, 3, tzinfo=UTC)
    try:
        async with database.session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_user(first_user_id, "BackfillFirst"))
            await users.create(make_user(second_user_id, "BackfillSecond"))
            await add_historical_match(
                session,
                match_id=match_id,
                first_user_id=first_user_id,
                second_user_id=second_user_id,
                completed_at=lower + timedelta(hours=12),
            )
            await session.commit()

        def factory() -> SqlAlchemyRatingsUnitOfWork:
            return SqlAlchemyRatingsUnitOfWork(database.session_factory)

        reports = await asyncio.gather(
            *(
                RatingHistoricalBackfill(
                    clock=lambda: upper,
                    completed_from=lower,
                    completed_before=upper,
                ).run(factory, dry_run=False)
                for _ in range(2)
            )
        )

        async with database.session_factory() as session:
            period = await session.scalar(
                select(RatingPeriodModel).where(RatingPeriodModel.starts_at == lower)
            )
            assert period is not None
            inputs = (
                await session.scalars(
                    select(RatingPeriodMatchInputModel).where(
                        RatingPeriodMatchInputModel.period_id == period.id
                    )
                )
            ).all()
            current = (
                await session.scalars(
                    select(RatingModel).where(
                        RatingModel.user_id.in_((first_user_id, second_user_id))
                    )
                )
            ).all()
        assert sum(report.processed_matches for report in reports) == 1
        assert len(inputs) == 1
        assert period.status is RatingPeriodStatus.PUBLISHED
        assert len(current) == 2
        assert {item.rated_matches_count for item in current} == {1}
    finally:
        async with database.session_factory() as session:
            period_ids = tuple(
                await session.scalars(
                    select(RatingPeriodModel.id).where(
                        RatingPeriodModel.starts_at >= lower,
                        RatingPeriodModel.starts_at < upper,
                    )
                )
            )
            await session.execute(
                delete(RatingModel).where(RatingModel.user_id.in_((first_user_id, second_user_id)))
            )
            if period_ids:
                await session.execute(
                    delete(RatingPeriodResultModel).where(
                        RatingPeriodResultModel.period_id.in_(period_ids)
                    )
                )
                await session.execute(
                    delete(RatingPeriodMatchInputModel).where(
                        RatingPeriodMatchInputModel.period_id.in_(period_ids)
                    )
                )
            await session.execute(delete(MatchSetModel).where(MatchSetModel.match_id == match_id))
            await session.execute(
                delete(MatchParticipantModel).where(MatchParticipantModel.match_id == match_id)
            )
            await session.execute(delete(MatchModel).where(MatchModel.id == match_id))
            if period_ids:
                await session.execute(
                    delete(RatingPeriodModel).where(RatingPeriodModel.id.in_(period_ids))
                )
            await session.execute(
                delete(UserModel).where(UserModel.id.in_((first_user_id, second_user_id)))
            )
            await session.commit()
        await database.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_rating_query_cannot_observe_partial_atomic_cutover() -> None:
    database = Database.from_settings(Settings())
    user_id, period_id = uuid4(), uuid4()
    starts_at = datetime(1901, 1, 1, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, period_id=period_id, algorithm_version="2"),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=2,
        published_version=1,
        published_at=starts_at + timedelta(days=1, minutes=5),
    )
    try:
        async with database.session_factory() as session:
            await SqlAlchemyUserRepository(session).create(make_user(user_id, "AtomicReader"))
            await SqlAlchemyRatingPeriodRepository(session).add(period)
            for version, rating_after in ((1, "1510.00000"), (2, "1710.00000")):
                session.add(
                    RatingPeriodResultModel(
                        id=uuid4(),
                        period_id=period.id,
                        calculation_version=version,
                        user_id=user_id,
                        rating_before=Decimal("1500.00000"),
                        rating_after=Decimal(rating_after),
                        rd_before=Decimal("350.00000"),
                        rd_after=Decimal("290.00000"),
                        volatility_before=Decimal("0.0600000000"),
                        volatility_after=Decimal("0.0600000000"),
                        games_count=1,
                        is_active=True,
                    )
                )
            session.add(
                RatingModel(
                    user_id=user_id,
                    rating=Decimal("1510.00000"),
                    rd=Decimal("290.00000"),
                    volatility=Decimal("0.0600000000"),
                    as_of_period_id=period.id,
                    rated_matches_count=1,
                    version=1,
                )
            )
            await session.commit()

        reader = database.session_factory()
        leaderboard = SqlAlchemyLeaderboardRepository(reader)
        leaderboard_before, leaderboard_total, leaderboard_position = await leaderboard.list_ranked(
            current_user_id=user_id,
            limit=20,
            offset=0,
        )
        queries = SqlAlchemyRatingQueryRepository(reader)
        before, _ = await queries.list_history(user_id, limit=20, offset=0)

        async def cutover() -> None:
            async with database.session_factory() as session:
                periods = SqlAlchemyRatingPeriodRepository(session)
                await periods.acquire_publication_lock()
                period_model = await session.get(RatingPeriodModel, period.id)
                rating_model = await session.get(RatingModel, user_id)
                assert period_model is not None and rating_model is not None
                period_model.published_version = 2
                rating_model.rating = Decimal("1710.00000")
                await session.commit()

        writer = asyncio.create_task(cutover())
        done, _ = await asyncio.wait({writer}, timeout=0.05)
        assert not done
        current_before = await queries.get_current(user_id)
        assert current_before is not None
        assert leaderboard_total == leaderboard_position == 1
        assert leaderboard_before[0].rating.rating == Decimal("1510.00000")
        assert before[0][0].rating_after == Decimal("1510.00000")
        assert current_before[0].rating == Decimal("1510.00000")
        await reader.rollback()
        await reader.close()
        await writer

        async with database.session_factory() as session:
            leaderboard_after, _, _ = await SqlAlchemyLeaderboardRepository(session).list_ranked(
                current_user_id=user_id,
                limit=20,
                offset=0,
            )
            after_queries = SqlAlchemyRatingQueryRepository(session)
            after, _ = await after_queries.list_history(user_id, limit=20, offset=0)
            current_after = await after_queries.get_current(user_id)
        assert after[0][0].rating_after == Decimal("1710.00000")
        assert leaderboard_after[0].rating.rating == Decimal("1710.00000")
        assert current_after is not None
        assert current_after[0].rating == Decimal("1710.00000")
    finally:
        async with database.session_factory() as session:
            await session.execute(delete(RatingModel).where(RatingModel.user_id == user_id))
            await session.execute(
                delete(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period_id
                )
            )
            await session.execute(
                delete(RatingPeriodModel).where(RatingPeriodModel.id == period_id)
            )
            await session.execute(delete(UserModel).where(UserModel.id == user_id))
            await session.commit()
        await database.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_concurrent_rebuild_orchestrators_share_one_atomic_result() -> None:
    database = Database.from_settings(Settings())
    first_user_id, second_user_id = uuid4(), uuid4()
    match_id, revision_id, period_id, job_id = uuid4(), uuid4(), uuid4(), uuid4()
    starts_at = datetime(1900, 1, 1, tzinfo=UTC)
    now = datetime(1900, 1, 3, tzinfo=UTC)
    period = replace(
        make_period(starts_at=starts_at, period_id=period_id, algorithm_version="2"),
        status=RatingPeriodStatus.CALCULATING,
        calculation_version=2,
        calculated_version=None,
    )
    job = RatingRebuildJob(
        id=job_id,
        from_period_id=period.id,
        through_period_id=period.id,
        status=RatingRebuildStatus.PENDING,
        singleton_scope=1,
        created_at=now,
        started_at=None,
        ready_at=None,
        completed_at=None,
        error_code=None,
        version=1,
    )
    checkpoint = RatingRebuildPeriod(
        rebuild_job_id=job.id,
        period_id=period.id,
        source_version=1,
        target_version=2,
        status=RatingRebuildPeriodStatus.PENDING,
        created_at=now,
        completed_at=None,
        error_code=None,
    )
    request = RatingRecalculationRequest(
        id=uuid4(),
        from_period_id=period.id,
        caused_by_match_id=match_id,
        caused_by_revision_id=revision_id,
        rebuild_job_id=job.id,
        status=RatingRecalculationStatus.PENDING,
        created_at=now,
        started_at=None,
        completed_at=None,
        error_code=None,
    )
    try:
        async with database.session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_user(first_user_id, "ShadowFirst"))
            await users.create(make_user(second_user_id, "ShadowSecond"))
            session.add(
                MatchModel(
                    id=match_id,
                    context=MatchContext.STANDALONE,
                    status=MatchStatus.COMPLETED,
                    created_by=first_user_id,
                    points_to_win=11,
                    win_by=2,
                    sets_to_win=1,
                    is_ranked=True,
                    winner_id=first_user_id,
                    started_at=starts_at + timedelta(hours=11),
                    completed_at=starts_at + timedelta(hours=12),
                    version=2,
                )
            )
            session.add_all(
                [
                    MatchParticipantModel(
                        id=uuid4(), match_id=match_id, user_id=first_user_id, position=1
                    ),
                    MatchParticipantModel(
                        id=uuid4(), match_id=match_id, user_id=second_user_id, position=2
                    ),
                ]
            )
            session.add(
                MatchSetModel(
                    id=uuid4(),
                    match_id=match_id,
                    set_number=1,
                    player1_score=11,
                    player2_score=4,
                )
            )
            session.add(
                MatchResultRevisionModel(
                    id=revision_id,
                    match_id=match_id,
                    revision_number=1,
                    changed_by=first_user_id,
                    previous_result={"winner_id": str(second_user_id), "sets": []},
                    new_result={"winner_id": str(first_user_id), "sets": []},
                    reason="concurrent cutover fixture",
                    operation=MatchRevisionOperation.CORRECTION,
                )
            )
            await session.commit()
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            await uow.periods.add(period)
            await uow.inputs.add(
                RatingPeriodMatchInput(
                    period_id=period.id,
                    calculation_version=1,
                    match_id=match_id,
                    source_match_version=2,
                    correction_revision_id=None,
                    result_fingerprint="c" * 64,
                    player1_id=first_user_id,
                    player2_id=second_user_id,
                    score1=Decimal("1.00"),
                    score2=Decimal("0.00"),
                    completed_at=starts_at + timedelta(hours=12),
                    created_at=now,
                )
            )
            await uow.rebuild_jobs.add(job)
            await uow.rebuild_periods.add(checkpoint)
            await uow.recalculations.add(request)
            await uow.commit()

        results = await asyncio.gather(
            *(
                RatingRebuildOrchestrator(clock=lambda: now + timedelta(hours=1)).run_once(
                    lambda: SqlAlchemyRatingsUnitOfWork(database.session_factory),
                )
                for _ in range(2)
            )
        )
        assert any(
            item is not None and item.status is RatingRebuildStatus.COMPLETED for item in results
        )
        async with SqlAlchemyRatingsUnitOfWork(database.session_factory) as uow:
            stored_checkpoint = await uow.rebuild_periods.get(job.id, period.id)
            shadow_inputs = tuple(await uow.inputs.list_for_period(period.id, 2))
            shadow_results = tuple(await uow.results.list_for_period(period.id, 2))
            activated_period = await uow.periods.get(period.id)
            activated_job = await uow.rebuild_jobs.get(job.id)
            current = tuple(await uow.ratings.list_all())
            barrier = await uow.rebuild_jobs.get_publication_barrier()
        assert stored_checkpoint is not None
        assert stored_checkpoint.status is RatingRebuildPeriodStatus.READY
        assert len(shadow_inputs) == 1
        assert len(shadow_results) == 2
        assert activated_period is not None and activated_period.published_version == 2
        assert activated_job is not None and activated_job.status is RatingRebuildStatus.COMPLETED
        assert len(current) == 2
        assert {item.rated_matches_count for item in current} == {1}
        assert barrier is None
    finally:
        async with database.session_factory() as session:
            await session.execute(
                delete(RatingModel).where(RatingModel.user_id.in_((first_user_id, second_user_id)))
            )
            await session.execute(
                delete(RatingPeriodResultModel).where(
                    RatingPeriodResultModel.period_id == period_id
                )
            )
            await session.execute(
                delete(RatingPeriodMatchInputModel).where(
                    RatingPeriodMatchInputModel.period_id == period_id
                )
            )
            await session.execute(
                delete(RatingRecalculationRequestModel).where(
                    RatingRecalculationRequestModel.rebuild_job_id == job_id
                )
            )
            await session.execute(
                delete(RatingRebuildPeriodModel).where(
                    RatingRebuildPeriodModel.rebuild_job_id == job_id
                )
            )
            await session.execute(
                delete(RatingRebuildJobModel).where(RatingRebuildJobModel.id == job_id)
            )
            await session.execute(delete(MatchSetModel).where(MatchSetModel.match_id == match_id))
            await session.execute(
                delete(MatchResultRevisionModel).where(
                    MatchResultRevisionModel.match_id == match_id
                )
            )
            await session.execute(
                delete(MatchParticipantModel).where(MatchParticipantModel.match_id == match_id)
            )
            await session.execute(delete(MatchModel).where(MatchModel.id == match_id))
            await session.execute(
                delete(RatingPeriodModel).where(RatingPeriodModel.id == period_id)
            )
            await session.execute(
                delete(UserModel).where(UserModel.id.in_((first_user_id, second_user_id)))
            )
            await session.commit()
        await database.dispose()


@pytest.mark.parametrize("published_version", [0, 2])
async def test_published_version_constraints(
    rating_database: RatingFixture,
    published_version: int,
) -> None:
    period = replace(
        make_period(starts_at=datetime(2102, 1, published_version + 1, tzinfo=UTC)),
        published_version=published_version,
    )
    async with rating_database.session_factory() as session:
        session.add(
            RatingPeriodModel(
                id=period.id,
                starts_at=period.starts_at,
                ends_at=period.ends_at,
                status=period.status,
                algorithm=period.algorithm,
                algorithm_version=period.algorithm_version,
                parameters=period.parameters,
                calculation_version=period.calculation_version,
                calculated_version=period.calculated_version,
                published_version=period.published_version,
                published_at=period.published_at,
                created_at=period.created_at,
                updated_at=period.updated_at,
                version=period.version,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
