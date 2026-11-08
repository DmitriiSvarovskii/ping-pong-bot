"""PostgreSQL transaction and race tests for game session lifecycle."""

import asyncio
import os
from collections import Counter
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.game_sessions.application.errors import (
    ActiveSessionParticipantExistsError,
    GameSessionCurrentPlayerLeaveError,
    GameSessionStateError,
)
from pingstats.modules.game_sessions.application.service import GameSessionService
from pingstats.modules.game_sessions.domain import GameSessionEventType, GameSessionStatus
from pingstats.modules.game_sessions.infrastructure.models import (
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyCurrentSessionMatchChecker,
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.game_sessions.infrastructure.uow import SqlAlchemyGameSessionsUnitOfWork
from pingstats.modules.matches.application.errors import MatchStateError
from pingstats.modules.matches.domain import SetScore
from pingstats.modules.matches.infrastructure.models import MatchModel
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.users.infrastructure.models import UserModel

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


@pytest.fixture
async def session_service_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]]
]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    user_ids = (uuid4(), uuid4(), uuid4())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            session.add_all(
                [
                    UserModel(id=user_id, first_name=f"Lifecycle {position}")
                    for position, user_id in enumerate(user_ids)
                ]
            )
            await session.commit()
        yield session_factory, user_ids
        await transaction.rollback()
    await engine.dispose()


class FailingEventRepository(SqlAlchemyGameSessionRepository):
    async def add_event(self, **kwargs):
        del kwargs
        raise RuntimeError("injected event failure")


class FailingEventUow(SqlAlchemyUnitOfWork):
    async def __aenter__(self) -> "FailingEventUow":
        await super().__aenter__()
        self.game_sessions = FailingEventRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = SqlAlchemyMatchRepository(self.session)
        return self


class FailingQueueInitializationRepository(SqlAlchemyGameSessionRepository):
    async def add_event(self, **kwargs):
        if kwargs["event_type"] is GameSessionEventType.QUEUE_INITIALIZED:
            raise RuntimeError("injected queue initialization failure")
        return await super().add_event(**kwargs)


class FailingQueueInitializationUow(SqlAlchemyUnitOfWork):
    async def __aenter__(self) -> "FailingQueueInitializationUow":
        await super().__aenter__()
        self.game_sessions = FailingQueueInitializationRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = SqlAlchemyMatchRepository(self.session)
        return self


class FailingSuccessorMatchRepository(SqlAlchemyMatchRepository):
    async def create_game_session_match(self, **kwargs):
        if kwargs["sequence_number"] > 1:
            raise RuntimeError("injected successor creation failure")
        return await super().create_game_session_match(**kwargs)


class FailingSuccessorUow(SqlAlchemyUnitOfWork):
    async def __aenter__(self) -> "FailingSuccessorUow":
        await super().__aenter__()
        self.game_sessions = SqlAlchemyGameSessionRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = FailingSuccessorMatchRepository(self.session)
        return self


async def test_lifecycle_history_events_and_create_rollback_are_transactional(
    session_service_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer, player, _) = session_service_database
    service = GameSessionService()

    with pytest.raises(RuntimeError, match="injected event failure"):
        await service.create_session(FailingEventUow(session_factory), actor_id=organizer)
    async with session_factory() as session:
        rolled_back = await session.scalar(
            select(func.count())
            .select_from(GameSessionModel)
            .where(GameSessionModel.organizer_id == organizer)
        )
        assert rolled_back == 0

    created = await service.create_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        name="Transactional session",
    )
    joined = await service.join_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
    )
    assert joined.session.version == 2
    await service.leave_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
    )
    await service.join_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
    )
    active = await service.start_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        session_id=created.session.id,
    )
    completed = await service.complete_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        session_id=created.session.id,
    )
    assert active.session.status is GameSessionStatus.ACTIVE
    assert completed.session.status is GameSessionStatus.COMPLETED

    async with session_factory() as session:
        repository = SqlAlchemyGameSessionRepository(session)
        history, history_total = await repository.list_participants(
            created.session.id,
            active_only=False,
            limit=20,
            offset=0,
        )
        events = (
            await session.scalars(
                select(GameSessionQueueEventModel)
                .where(GameSessionQueueEventModel.session_id == created.session.id)
                .order_by(GameSessionQueueEventModel.created_at, GameSessionQueueEventModel.id)
            )
        ).all()
        queue_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueModel)
            .where(GameSessionQueueModel.session_id == created.session.id)
        )

    assert history_total == 3 and len(history) == 3
    assert Counter(event.event_type for event in events) == Counter(
        {
            GameSessionEventType.CREATED.value: 1,
            GameSessionEventType.JOINED.value: 3,
            GameSessionEventType.LEFT.value: 1,
            GameSessionEventType.STARTED.value: 1,
            GameSessionEventType.QUEUE_INITIALIZED.value: 1,
            GameSessionEventType.MATCH_STARTED.value: 1,
            GameSessionEventType.MATCH_CANCELLED.value: 1,
            GameSessionEventType.COMPLETED.value: 1,
        }
    )
    assert queue_count == 0


async def test_queue_rotation_leave_restore_and_idempotency_are_transactional(
    session_service_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer, player, challenger) = session_service_database
    service = GameSessionService()
    created = await service.create_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory), actor_id=organizer
    )
    for user_id in (player, challenger):
        await service.join_session(
            SqlAlchemyGameSessionsUnitOfWork(session_factory),
            actor_id=user_id,
            session_id=created.session.id,
        )

    with pytest.raises(RuntimeError, match="queue initialization failure"):
        await service.start_session(
            FailingQueueInitializationUow(session_factory),
            actor_id=organizer,
            session_id=created.session.id,
        )
    async with session_factory() as session:
        stored = await session.get(GameSessionModel, created.session.id)
        pair = await session.get(GameSessionCurrentPairModel, created.session.id)
        assert stored is not None and stored.status is GameSessionStatus.PENDING
        assert pair is None

    await service.start_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=organizer,
        session_id=created.session.id,
    )
    async with session_factory() as session:
        first_match = await SqlAlchemyMatchRepository(session).get_current_for_session(
            created.session.id
        )
    assert first_match is not None
    before_state: object
    async with session_factory() as session:
        before_state = await SqlAlchemyGameSessionRepository(session).get_queue_state(
            created.session.id
        )
    with pytest.raises(RuntimeError, match="successor creation failure"):
        await service.skip_current_match(
            FailingSuccessorUow(session_factory),
            actor_id=organizer,
            session_id=created.session.id,
            match_id=first_match.match.id,
            skipped_player_id=organizer,
            skip_both=False,
        )
    with pytest.raises(RuntimeError, match="successor creation failure"):
        await service.record_session_result(
            FailingSuccessorUow(session_factory),
            actor_id=player,
            session_id=created.session.id,
            match_id=first_match.match.id,
            scores=[SetScore(11, 4)],
        )
    async with session_factory() as session:
        match_after_rollback = await SqlAlchemyMatchRepository(session).get_view(
            first_match.match.id
        )
        queue_after_rollback = await SqlAlchemyGameSessionRepository(session).get_queue_state(
            created.session.id
        )
    assert match_after_rollback is not None
    assert match_after_rollback.match.status.value == "active"
    assert match_after_rollback.sets == ()
    assert queue_after_rollback == before_state
    async with session_factory() as session:
        skipped_event_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueEventModel)
            .where(
                GameSessionQueueEventModel.session_id == created.session.id,
                GameSessionQueueEventModel.event_type
                == GameSessionEventType.CURRENT_GAME_SKIPPED.value,
            )
        )
    assert skipped_event_count == 0

    rotated = await service.record_session_result(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
        match_id=first_match.match.id,
        scores=[SetScore(11, 4)],
    )
    assert rotated.queue is not None
    assert tuple(item.user_id for item in rotated.queue.current_pair) == (organizer, challenger)
    assert [item.user_id for item in rotated.queue.waiting] == [player]

    repeated = await service.record_session_result(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
        match_id=first_match.match.id,
        scores=[SetScore(11, 4)],
    )
    assert repeated == rotated
    await service.leave_session(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=player,
        session_id=created.session.id,
    )
    assert rotated.current_match is not None
    restored = await service.cancel_current_match(
        SqlAlchemyGameSessionsUnitOfWork(session_factory),
        actor_id=challenger,
        session_id=created.session.id,
        match_id=rotated.current_match.match.id,
    )
    assert restored.queue is not None
    assert tuple(item.user_id for item in restored.queue.current_pair) == (
        organizer,
        challenger,
    )
    assert restored.queue.waiting == ()

    async with session_factory() as session:
        processed_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionProcessedResultModel)
            .where(GameSessionProcessedResultModel.session_id == created.session.id)
        )
        rotation_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueEventModel)
            .where(
                GameSessionQueueEventModel.session_id == created.session.id,
                GameSessionQueueEventModel.event_type == GameSessionEventType.ROTATION.value,
            )
        )
        queue_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueModel)
            .where(GameSessionQueueModel.session_id == created.session.id)
        )
    assert processed_count == 1
    assert rotation_count == 1
    assert queue_count == 0


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_concurrent_join_start_and_join_vs_complete_are_serialized() -> None:
    database = Database.from_settings(Settings())
    organizer, player, late_player = uuid4(), uuid4(), uuid4()
    service = GameSessionService()
    session_id: UUID | None = None
    try:
        async with database.session_factory() as session:
            session.add_all(
                [
                    UserModel(id=organizer, first_name="Race organizer"),
                    UserModel(id=player, first_name="Race player"),
                    UserModel(id=late_player, first_name="Race late player"),
                ]
            )
            await session.commit()

        created = await service.create_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
            actor_id=organizer,
        )
        session_id = created.session.id
        join_results = await asyncio.gather(
            *(
                service.join_session(
                    SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                    actor_id=player,
                    session_id=session_id,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert len([item for item in join_results if not isinstance(item, BaseException)]) == 1
        assert (
            len(
                [
                    item
                    for item in join_results
                    if isinstance(item, ActiveSessionParticipantExistsError)
                ]
            )
            == 1
        )

        start_results = await asyncio.gather(
            *(
                service.start_session(
                    SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                    actor_id=organizer,
                    session_id=session_id,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert len([item for item in start_results if not isinstance(item, BaseException)]) == 1
        assert len([item for item in start_results if isinstance(item, GameSessionStateError)]) == 1

        completion_race = await asyncio.gather(
            service.join_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=late_player,
                session_id=session_id,
            ),
            service.complete_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=organizer,
                session_id=session_id,
            ),
            return_exceptions=True,
        )
        assert not isinstance(completion_race[1], BaseException)
        assert isinstance(completion_race[0], (GameSessionStateError, type(completion_race[1])))

        async with database.session_factory() as session:
            stored = await session.get(GameSessionModel, session_id)
            active_player_count = await session.scalar(
                select(func.count())
                .select_from(GameSessionParticipantModel)
                .where(
                    GameSessionParticipantModel.session_id == session_id,
                    GameSessionParticipantModel.user_id == player,
                    GameSessionParticipantModel.left_at.is_(None),
                )
            )
            event_count = await session.scalar(
                select(func.count())
                .select_from(GameSessionQueueEventModel)
                .where(GameSessionQueueEventModel.session_id == session_id)
            )
        assert stored is not None and stored.status is GameSessionStatus.COMPLETED
        assert active_player_count == 1
        assert event_count in (8, 10)
    finally:
        async with database.session_factory() as session:
            if session_id is not None:
                await session.execute(
                    delete(GameSessionProcessedResultModel).where(
                        GameSessionProcessedResultModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(GameSessionQueueEventModel).where(
                        GameSessionQueueEventModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(GameSessionQueueModel).where(
                        GameSessionQueueModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(GameSessionCurrentPairModel).where(
                        GameSessionCurrentPairModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(MatchModel).where(MatchModel.game_session_id == session_id)
                )
                await session.execute(
                    delete(GameSessionParticipantModel).where(
                        GameSessionParticipantModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(GameSessionModel).where(GameSessionModel.id == session_id)
                )
            await session.execute(
                delete(UserModel).where(UserModel.id.in_((organizer, player, late_player)))
            )
            await session.commit()
        await database.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_result_vs_cancel_and_result_vs_complete_are_serialized() -> None:
    database = Database.from_settings(Settings())
    organizer, player = uuid4(), uuid4()
    service = GameSessionService()
    session_ids: list[UUID] = []

    async def create_active_session():
        created = await service.create_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory), actor_id=organizer
        )
        session_ids.append(created.session.id)
        await service.join_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
            actor_id=player,
            session_id=created.session.id,
        )
        await service.start_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
            actor_id=organizer,
            session_id=created.session.id,
        )
        async with database.session_factory() as session:
            current = await SqlAlchemyMatchRepository(session).get_current_for_session(
                created.session.id
            )
        assert current is not None
        return created.session.id, current

    try:
        async with database.session_factory() as session:
            session.add_all(
                [
                    UserModel(id=organizer, first_name="Result race organizer"),
                    UserModel(id=player, first_name="Result race player"),
                ]
            )
            await session.commit()

        cancel_session_id, cancel_match = await create_active_session()
        cancel_race = await asyncio.gather(
            service.record_session_result(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=player,
                session_id=cancel_session_id,
                match_id=cancel_match.match.id,
                scores=[SetScore(11, 4)],
            ),
            service.cancel_current_match(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=player,
                session_id=cancel_session_id,
                match_id=cancel_match.match.id,
            ),
            return_exceptions=True,
        )
        assert len([item for item in cancel_race if not isinstance(item, BaseException)]) == 1
        assert len([item for item in cancel_race if isinstance(item, MatchStateError)]) == 1

        complete_session_id, complete_match = await create_active_session()
        complete_race = await asyncio.gather(
            service.record_session_result(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=player,
                session_id=complete_session_id,
                match_id=complete_match.match.id,
                scores=[SetScore(11, 5)],
            ),
            service.complete_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=organizer,
                session_id=complete_session_id,
            ),
            return_exceptions=True,
        )
        assert not isinstance(complete_race[1], BaseException)
        assert not isinstance(complete_race[0], BaseException) or isinstance(
            complete_race[0], GameSessionStateError
        )
        async with database.session_factory() as session:
            stored = await session.get(GameSessionModel, complete_session_id)
            active_count = await session.scalar(
                select(func.count())
                .select_from(MatchModel)
                .where(
                    MatchModel.game_session_id == complete_session_id,
                    MatchModel.status == "active",
                )
            )
        assert stored is not None and stored.status is GameSessionStatus.COMPLETED
        assert active_count == 0

        skip_session_id, skip_match = await create_active_session()
        skip_race = await asyncio.gather(
            *(
                service.skip_current_match(
                    SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                    actor_id=organizer,
                    session_id=skip_session_id,
                    match_id=skip_match.match.id,
                    skipped_player_id=None,
                    skip_both=True,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert len([item for item in skip_race if not isinstance(item, BaseException)]) == 1
        assert len([item for item in skip_race if isinstance(item, MatchStateError)]) == 1
        async with database.session_factory() as session:
            skip_active_count = await session.scalar(
                select(func.count())
                .select_from(MatchModel)
                .where(
                    MatchModel.game_session_id == skip_session_id,
                    MatchModel.status == "active",
                )
            )
        assert skip_active_count == 1
    finally:
        async with database.session_factory() as session:
            for session_id in session_ids:
                for model in (
                    GameSessionProcessedResultModel,
                    GameSessionQueueEventModel,
                    GameSessionQueueModel,
                    GameSessionCurrentPairModel,
                ):
                    await session.execute(delete(model).where(model.session_id == session_id))
                await session.execute(
                    delete(MatchModel).where(MatchModel.game_session_id == session_id)
                )
                await session.execute(
                    delete(GameSessionParticipantModel).where(
                        GameSessionParticipantModel.session_id == session_id
                    )
                )
                await session.execute(
                    delete(GameSessionModel).where(GameSessionModel.id == session_id)
                )
            await session.execute(delete(UserModel).where(UserModel.id.in_((organizer, player))))
            await session.commit()
        await database.dispose()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_concurrent_rotation_join_leave_and_result_retry_preserve_queue() -> None:
    database = Database.from_settings(Settings())
    organizer, second, third, fourth, late = (uuid4() for _ in range(5))
    user_ids = (organizer, second, third, fourth, late)
    service = GameSessionService()
    session_id: UUID | None = None
    try:
        async with database.session_factory() as session:
            session.add_all(
                [
                    UserModel(id=user_id, first_name=f"Queue race {position}")
                    for position, user_id in enumerate(user_ids)
                ]
            )
            await session.commit()
        created = await service.create_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
            actor_id=organizer,
        )
        session_id = created.session.id
        for user_id in (second, third, fourth):
            await service.join_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=user_id,
                session_id=session_id,
            )
        await service.start_session(
            SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
            actor_id=organizer,
            session_id=session_id,
        )

        async with database.session_factory() as session:
            first_match = await SqlAlchemyMatchRepository(session).get_current_for_session(
                session_id
            )
        assert first_match is not None
        repeated = await asyncio.gather(
            *(
                service.record_session_result(
                    SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                    actor_id=actor_id,
                    session_id=session_id,
                    match_id=first_match.match.id,
                    scores=[SetScore(11, 4)],
                )
                for actor_id in (third, fourth)
            )
        )
        assert repeated[0] == repeated[1]

        assert repeated[0].current_match is not None
        join_rotation = await asyncio.gather(
            service.join_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=late,
                session_id=session_id,
            ),
            service.record_session_result(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=third,
                session_id=session_id,
                match_id=repeated[0].current_match.match.id,
                scores=[SetScore(11, 5)],
            ),
        )
        assert all(not isinstance(item, BaseException) for item in join_rotation)

        result_transition = join_rotation[1]
        assert result_transition.current_match is not None
        leave_rotation = await asyncio.gather(
            service.leave_session(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=second,
                session_id=session_id,
            ),
            service.record_session_result(
                SqlAlchemyGameSessionsUnitOfWork(database.session_factory),
                actor_id=third,
                session_id=session_id,
                match_id=result_transition.current_match.match.id,
                scores=[SetScore(11, 6)],
            ),
            return_exceptions=True,
        )
        assert not isinstance(leave_rotation[1], BaseException)
        assert not isinstance(leave_rotation[0], BaseException) or isinstance(
            leave_rotation[0], GameSessionCurrentPlayerLeaveError
        )

        async with database.session_factory() as session:
            repository = SqlAlchemyGameSessionRepository(session)
            state = await repository.get_queue_state(session_id)
            participants = await repository.list_active_participants(session_id)
            processed_count = await session.scalar(
                select(func.count())
                .select_from(GameSessionProcessedResultModel)
                .where(GameSessionProcessedResultModel.session_id == session_id)
            )
            rotation_count = await session.scalar(
                select(func.count())
                .select_from(GameSessionQueueEventModel)
                .where(
                    GameSessionQueueEventModel.session_id == session_id,
                    GameSessionQueueEventModel.event_type == GameSessionEventType.ROTATION.value,
                )
            )
        assert state is not None
        state.validate()
        assert set((*state.current_pair, *state.waiting)) == {
            participant.id for participant in participants
        }
        assert processed_count == 3
        assert rotation_count == 3
    finally:
        async with database.session_factory() as session:
            if session_id is not None:
                for model in (
                    GameSessionProcessedResultModel,
                    GameSessionQueueEventModel,
                    GameSessionQueueModel,
                    GameSessionCurrentPairModel,
                    GameSessionParticipantModel,
                ):
                    await session.execute(delete(model).where(model.session_id == session_id))
                await session.execute(
                    delete(MatchModel).where(MatchModel.game_session_id == session_id)
                )
                await session.execute(
                    delete(GameSessionModel).where(GameSessionModel.id == session_id)
                )
            await session.execute(delete(UserModel).where(UserModel.id.in_(user_ids)))
            await session.commit()
        await database.dispose()
