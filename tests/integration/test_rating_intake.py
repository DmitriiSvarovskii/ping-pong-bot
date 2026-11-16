"""PostgreSQL transaction tests for atomic rating match intake."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.game_sessions.application.service import GameSessionService
from pingstats.modules.game_sessions.infrastructure.models import (
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.game_sessions.infrastructure.uow import SqlAlchemyGameSessionsUnitOfWork
from pingstats.modules.matches.application.result_writer import MatchResultWriter
from pingstats.modules.matches.application.service import MatchService
from pingstats.modules.matches.domain import MatchStatus, SetScore
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchResultRevisionModel,
    MatchSetModel,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.matches.infrastructure.uow import SqlAlchemyMatchesUnitOfWork
from pingstats.modules.ratings.application import (
    RatingMatchInputRecorder,
    RatingPeriodCloser,
    RatingPeriodUnavailableError,
)
from pingstats.modules.ratings.domain import RatingPeriodStatus
from pingstats.modules.ratings.infrastructure.models import (
    RatingPeriodMatchInputModel,
    RatingPeriodModel,
)
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingInputRepository,
    SqlAlchemyRatingPeriodRepository,
)
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


def make_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        user_id,
        None,
        name,
        "IntakeTest",
        f"intake_{user_id.hex}",
        None,
        now,
        now,
        None,
    )


@pytest.fixture
async def intake_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]]
]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    connection = await engine.connect()
    transaction = await connection.begin()
    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    user_ids = (uuid4(), uuid4(), uuid4())
    async with session_factory() as session:
        users = SqlAlchemyUserRepository(session)
        for position, user_id in enumerate(user_ids, start=1):
            await users.create(make_user(user_id, f"Player{position}"))
        await session.commit()
    try:
        yield session_factory, user_ids
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


def match_service_at(completed_at: datetime) -> MatchService:
    def clock() -> datetime:
        return completed_at

    return MatchService(
        result_writer=MatchResultWriter(clock=clock),
        rating_input_recorder=RatingMatchInputRecorder(clock=clock),
    )


async def active_standalone(
    service: MatchService,
    session_factory: async_sessionmaker[AsyncSession],
    creator_id: UUID,
    invited_id: UUID,
    *,
    is_ranked: bool = True,
):
    pending = await service.create_standalone(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        creator_id=creator_id,
        invited_user_id=invited_id,
        sets_to_win=1,
        is_ranked=is_ranked,
    )
    return await service.accept(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited_id,
        match_id=pending.match.id,
    )


async def test_standalone_ranked_intake_is_atomic_idempotent_and_filters_ineligible(
    intake_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (first, second, _) = intake_database
    completed_at = datetime(2091, 5, 2, 0, 0, tzinfo=UTC)
    service = match_service_at(completed_at)
    active = await active_standalone(service, session_factory, first, second)
    completed = await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=first,
        match_id=active.match.id,
        scores=[SetScore(11, 7)],
    )
    repeated = await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=second,
        match_id=active.match.id,
        scores=[SetScore(11, 7)],
    )

    unranked = await active_standalone(service, session_factory, first, second, is_ranked=False)
    await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=first,
        match_id=unranked.match.id,
        scores=[SetScore(11, 8)],
    )
    still_active = await active_standalone(service, session_factory, first, second)
    cancelled = await active_standalone(service, session_factory, first, second)
    await service.cancel(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=first,
        match_id=cancelled.match.id,
    )

    async with session_factory() as session:
        inputs = (
            await session.scalars(
                select(RatingPeriodMatchInputModel).where(
                    RatingPeriodMatchInputModel.match_id.in_(
                        (
                            completed.match.id,
                            unranked.match.id,
                            still_active.match.id,
                            cancelled.match.id,
                        )
                    )
                )
            )
        ).all()
        period = await session.get(RatingPeriodModel, inputs[0].period_id)

    assert repeated == completed
    assert len(inputs) == 1
    assert inputs[0].match_id == completed.match.id
    assert inputs[0].source_match_version == completed.match.version
    assert period is not None and period.starts_at == completed_at
    assert still_active.match.status is MatchStatus.ACTIVE


async def test_ranked_session_result_records_input_and_rotates_atomically(
    intake_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer, second, waiting) = intake_database
    completed_at = datetime(2092, 6, 3, 15, 0, tzinfo=UTC)
    service = GameSessionService(clock=lambda: completed_at)
    created = await service.create_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory), actor_id=organizer
    )
    for user_id in (second, waiting):
        await service.join_session(
            SqlAlchemyGameSessionsUnitOfWork(session_factory),
            actor_id=user_id,
            session_id=created.session.id,
        )
    await service.start_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        session_id=created.session.id,
    )
    async with session_factory() as session:
        current = await SqlAlchemyMatchRepository(session).get_current_for_session(
            created.session.id
        )
    assert current is not None

    result = await service.record_session_result(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=second,
        session_id=created.session.id,
        match_id=current.match.id,
        scores=[SetScore(11, 6)],
    )

    async with session_factory() as session:
        rating_input = await session.scalar(
            select(RatingPeriodMatchInputModel).where(
                RatingPeriodMatchInputModel.match_id == current.match.id
            )
        )
        processed = await session.scalar(
            select(GameSessionProcessedResultModel).where(
                GameSessionProcessedResultModel.session_id == created.session.id,
                GameSessionProcessedResultModel.result_id == current.match.id,
            )
        )
    assert rating_input is not None
    assert processed is not None
    assert result.match.match.status is MatchStatus.COMPLETED
    assert result.current_match is not None
    assert result.current_match.match.id != current.match.id


class FailingRatingInputRepository(SqlAlchemyRatingInputRepository):
    async def add_if_absent(self, item):
        del item
        raise RuntimeError("injected rating input failure")


class FailingMatchesRatingUow(SqlAlchemyMatchesUnitOfWork):
    async def __aenter__(self) -> "FailingMatchesRatingUow":
        await super().__aenter__()
        self.rating_inputs = FailingRatingInputRepository(self.session)
        return self


class FailingSessionRatingUow(SqlAlchemyGameSessionsUnitOfWork):
    async def __aenter__(self) -> "FailingSessionRatingUow":
        await super().__aenter__()
        self.rating_inputs = FailingRatingInputRepository(self.session)
        return self


class BlockingRatingPeriodRepository(SqlAlchemyRatingPeriodRepository):
    def __init__(self, session, lock_acquired: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(session)
        self._lock_acquired = lock_acquired
        self._release = release

    async def acquire_period_lock(self, starts_at: datetime) -> None:
        await super().acquire_period_lock(starts_at)
        self._lock_acquired.set()
        await self._release.wait()


class BlockingCloserUow(SqlAlchemyRatingsUnitOfWork):
    def __init__(
        self,
        session_factory,
        lock_acquired: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        super().__init__(session_factory)
        self._lock_acquired = lock_acquired
        self._release = release

    async def __aenter__(self) -> "BlockingCloserUow":
        await super().__aenter__()
        self.periods = BlockingRatingPeriodRepository(
            self.session,
            self._lock_acquired,
            self._release,
        )
        return self


async def test_rating_input_failure_rolls_back_standalone_result(
    intake_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (first, second, _) = intake_database
    service = match_service_at(datetime(2093, 7, 4, 12, tzinfo=UTC))
    active = await active_standalone(service, session_factory, first, second)

    with pytest.raises(RuntimeError, match="injected rating input failure"):
        await service.record_full_result(
            FailingMatchesRatingUow(session_factory),
            actor_id=first,
            match_id=active.match.id,
            scores=[SetScore(11, 5)],
        )

    async with session_factory() as session:
        view = await SqlAlchemyMatchRepository(session).get_view(active.match.id)
        inputs = await session.scalar(
            select(func.count())
            .select_from(RatingPeriodMatchInputModel)
            .where(RatingPeriodMatchInputModel.match_id == active.match.id)
        )
        period = await session.scalar(
            select(RatingPeriodModel).where(
                RatingPeriodModel.starts_at == datetime(2093, 7, 4, tzinfo=UTC)
            )
        )
    assert view is not None and view.match.status is MatchStatus.ACTIVE
    assert not view.sets
    assert inputs == 0 and period is None


async def test_rating_input_failure_rolls_back_session_rotation(
    intake_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer, second, waiting) = intake_database
    completed_at = datetime(2094, 8, 5, 12, tzinfo=UTC)
    service = GameSessionService(clock=lambda: completed_at)
    created = await service.create_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory), actor_id=organizer
    )
    for user_id in (second, waiting):
        await service.join_session(
            SqlAlchemyGameSessionsUnitOfWork(session_factory),
            actor_id=user_id,
            session_id=created.session.id,
        )
    await service.start_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        session_id=created.session.id,
    )
    async with session_factory() as session:
        sessions = SqlAlchemyGameSessionRepository(session)
        queue_before = await sessions.get_queue_state(created.session.id)
        current = await SqlAlchemyMatchRepository(session).get_current_for_session(
            created.session.id
        )
    assert current is not None

    with pytest.raises(RuntimeError, match="injected rating input failure"):
        await service.record_session_result(
            FailingSessionRatingUow(session_factory),
            actor_id=organizer,
            session_id=created.session.id,
            match_id=current.match.id,
            scores=[SetScore(11, 5)],
        )

    async with session_factory() as session:
        sessions = SqlAlchemyGameSessionRepository(session)
        queue_after = await sessions.get_queue_state(created.session.id)
        match_after = await SqlAlchemyMatchRepository(session).get_view(current.match.id)
        processed = await session.scalar(
            select(func.count())
            .select_from(GameSessionProcessedResultModel)
            .where(GameSessionProcessedResultModel.result_id == current.match.id)
        )
    assert queue_after == queue_before
    assert match_after is not None and match_after.match.status is MatchStatus.ACTIVE
    assert not match_after.sets and processed == 0


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed rows in the local development database",
)
async def test_concurrent_completion_and_period_creation_are_idempotent() -> None:
    database = Database.from_settings(Settings())
    first, second = uuid4(), uuid4()
    completed_at = datetime(2099, 9, 6, 12, tzinfo=UTC)
    service = match_service_at(completed_at)
    match_ids: list[UUID] = []
    period_ids: set[UUID] = set()
    try:
        async with database.session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_user(first, "ConcurrentFirst"))
            await users.create(make_user(second, "ConcurrentSecond"))
            await session.commit()

        active_matches = [
            await active_standalone(service, database.session_factory, first, second)
            for _ in range(4)
        ]
        match_ids = [item.match.id for item in active_matches]
        scores = [SetScore(11, 5)]

        same_match_results = await asyncio.gather(
            *(
                service.record_full_result(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=actor,
                    match_id=active_matches[0].match.id,
                    scores=scores,
                )
                for actor in (first, second)
            )
        )
        await asyncio.gather(
            *(
                service.record_full_result(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=first,
                    match_id=match.match.id,
                    scores=scores,
                )
                for match in active_matches[1:]
            )
        )

        async with database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(RatingPeriodMatchInputModel).where(
                        RatingPeriodMatchInputModel.match_id.in_(match_ids)
                    )
                )
            ).all()
            period_count = await session.scalar(
                select(func.count())
                .select_from(RatingPeriodModel)
                .where(RatingPeriodModel.starts_at == datetime(2099, 9, 6, tzinfo=UTC))
            )
        period_ids = {item.period_id for item in rows}
        assert all(item.match.status is MatchStatus.COMPLETED for item in same_match_results)
        assert len(rows) == len(match_ids) == 4
        assert len(period_ids) == period_count == 1

        boundary_match = await active_standalone(service, database.session_factory, first, second)
        match_ids.append(boundary_match.match.id)
        lock_acquired = asyncio.Event()
        release_closer = asyncio.Event()

        async def close_period() -> None:
            result = await RatingPeriodCloser().close_period(
                BlockingCloserUow(
                    database.session_factory,
                    lock_acquired,
                    release_closer,
                ),
                starts_at=datetime(2099, 9, 6, tzinfo=UTC),
                now=datetime(2099, 9, 7, 0, 5, tzinfo=UTC),
            )
            assert result is not None and result.status is RatingPeriodStatus.CALCULATING

        closer = asyncio.create_task(close_period())
        await lock_acquired.wait()
        late_completion = asyncio.create_task(
            service.record_full_result(
                SqlAlchemyMatchesUnitOfWork(database.session_factory),
                actor_id=first,
                match_id=boundary_match.match.id,
                scores=scores,
            )
        )
        await asyncio.sleep(0)
        release_closer.set()
        await closer
        with pytest.raises(RatingPeriodUnavailableError):
            await late_completion

        async with database.session_factory() as session:
            boundary_view = await SqlAlchemyMatchRepository(session).get_view(
                boundary_match.match.id
            )
            boundary_input_count = await session.scalar(
                select(func.count())
                .select_from(RatingPeriodMatchInputModel)
                .where(RatingPeriodMatchInputModel.match_id == boundary_match.match.id)
            )
        assert boundary_view is not None
        assert boundary_view.match.status is MatchStatus.ACTIVE
        assert not boundary_view.sets and boundary_input_count == 0
    finally:
        async with database.session_factory() as session:
            if match_ids:
                await session.execute(
                    delete(RatingPeriodMatchInputModel).where(
                        RatingPeriodMatchInputModel.match_id.in_(match_ids)
                    )
                )
                await session.execute(
                    delete(MatchResultRevisionModel).where(
                        MatchResultRevisionModel.match_id.in_(match_ids)
                    )
                )
                await session.execute(
                    delete(MatchSetModel).where(MatchSetModel.match_id.in_(match_ids))
                )
                await session.execute(
                    delete(MatchParticipantModel).where(
                        MatchParticipantModel.match_id.in_(match_ids)
                    )
                )
                await session.execute(delete(MatchModel).where(MatchModel.id.in_(match_ids)))
            if period_ids:
                await session.execute(
                    delete(RatingPeriodModel).where(RatingPeriodModel.id.in_(period_ids))
                )
            session_ids = select(GameSessionModel.id).where(
                GameSessionModel.organizer_id.in_((first, second))
            )
            await session.execute(
                delete(GameSessionProcessedResultModel).where(
                    GameSessionProcessedResultModel.session_id.in_(session_ids)
                )
            )
            await session.execute(
                delete(GameSessionCurrentPairModel).where(
                    GameSessionCurrentPairModel.session_id.in_(session_ids)
                )
            )
            await session.execute(
                delete(GameSessionQueueModel).where(
                    GameSessionQueueModel.session_id.in_(session_ids)
                )
            )
            await session.execute(
                delete(GameSessionQueueEventModel).where(
                    GameSessionQueueEventModel.session_id.in_(session_ids)
                )
            )
            await session.execute(
                delete(GameSessionParticipantModel).where(
                    GameSessionParticipantModel.session_id.in_(session_ids)
                )
            )
            await session.execute(
                delete(GameSessionModel).where(GameSessionModel.id.in_(session_ids))
            )
            await session.execute(delete(UserModel).where(UserModel.id.in_((first, second))))
            await session.commit()
