"""Transactional PostgreSQL constraint tests for game session storage."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.modules.game_sessions.domain import GameSessionStatus
from pingstats.modules.game_sessions.infrastructure.models import (
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.matches.domain import MatchContext, MatchStatus
from pingstats.modules.matches.infrastructure.models import MatchModel, MatchParticipantModel
from pingstats.modules.users.infrastructure.models import UserModel

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


@pytest.fixture
async def session_database() -> AsyncIterator[
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
                    UserModel(id=user_id, first_name=f"Session {position}")
                    for position, user_id in enumerate(user_ids)
                ]
            )
            await session.commit()
        yield session_factory, user_ids
        await transaction.rollback()
    await engine.dispose()


async def create_session(
    session_factory: async_sessionmaker[AsyncSession], organizer_id: UUID
) -> UUID:
    session_id = uuid4()
    async with session_factory() as session:
        session.add(GameSessionModel(id=session_id, organizer_id=organizer_id))
        await session.commit()
    return session_id


async def test_session_defaults_checks_foreign_key_and_participation_history(
    session_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer_id, player_id, _) = session_database
    session_id = await create_session(session_factory, organizer_id)
    participant_id = uuid4()

    async with session_factory() as session:
        stored = await session.get(GameSessionModel, session_id)
        assert stored is not None
        assert (stored.status, stored.points_to_win, stored.sets_to_win, stored.is_ranked) == (
            GameSessionStatus.PENDING,
            11,
            1,
            True,
        )
        session.add(
            GameSessionParticipantModel(
                id=participant_id,
                session_id=session_id,
                user_id=player_id,
            )
        )
        await session.commit()

    async with session_factory() as session:
        session.add(
            GameSessionParticipantModel(
                session_id=session_id,
                user_id=player_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    left_at = datetime.now(UTC)
    async with session_factory() as session:
        participant = await session.get(GameSessionParticipantModel, participant_id)
        assert participant is not None
        participant.left_at = left_at
        session.add(
            GameSessionParticipantModel(
                session_id=session_id,
                user_id=player_id,
                joined_at=left_at,
            )
        )
        await session.commit()
        active_count = len(
            (
                await session.scalars(
                    select(GameSessionParticipantModel).where(
                        GameSessionParticipantModel.session_id == session_id,
                        GameSessionParticipantModel.user_id == player_id,
                        GameSessionParticipantModel.left_at.is_(None),
                    )
                )
            ).all()
        )
        assert active_count == 1

    async with session_factory() as session:
        session.add(GameSessionModel(organizer_id=uuid4()))
        with pytest.raises(IntegrityError):
            await session.commit()

    async with session_factory() as session:
        session.add(GameSessionModel(organizer_id=organizer_id, points_to_win=0))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_queue_constraints_events_and_deferrable_atomic_reorder(
    session_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer_id, second_id, third_id) = session_database
    session_id = await create_session(session_factory, organizer_id)
    participant_ids = (uuid4(), uuid4(), uuid4())
    queue_ids = (uuid4(), uuid4())

    async with session_factory() as session:
        session.add_all(
            [
                GameSessionParticipantModel(
                    id=participant_id,
                    session_id=session_id,
                    user_id=user_id,
                )
                for participant_id, user_id in zip(
                    participant_ids,
                    (organizer_id, second_id, third_id),
                    strict=True,
                )
            ]
        )
        session.add_all(
            [
                GameSessionQueueModel(
                    id=queue_ids[0],
                    session_id=session_id,
                    participant_id=participant_ids[0],
                    position=1,
                ),
                GameSessionQueueModel(
                    id=queue_ids[1],
                    session_id=session_id,
                    participant_id=participant_ids[1],
                    position=2,
                ),
                GameSessionQueueEventModel(
                    session_id=session_id,
                    participant_id=participant_ids[0],
                    event_type="joined",
                    payload={"position": 1},
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        session.add(
            GameSessionQueueModel(
                session_id=session_id,
                participant_id=participant_ids[2],
                position=2,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    async with session_factory() as session:
        session.add(
            GameSessionQueueModel(
                session_id=session_id,
                participant_id=participant_ids[0],
                position=3,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    async with session_factory() as session:
        session.add(
            GameSessionQueueModel(
                session_id=session_id,
                participant_id=participant_ids[2],
                position=0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    async with session_factory() as session:
        await session.execute(
            text("SET CONSTRAINTS uq_game_session_queue_session_position DEFERRED")
        )
        first = await session.get(GameSessionQueueModel, queue_ids[0])
        second = await session.get(GameSessionQueueModel, queue_ids[1])
        assert first is not None and second is not None
        first.position, second.position = second.position, first.position
        await session.commit()


async def test_standalone_compatibility_and_game_session_sequence_constraints(
    session_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer_id, second_id, _) = session_database
    session_id = await create_session(session_factory, organizer_id)
    standalone_id = uuid4()

    async with session_factory() as session:
        session.add(
            MatchModel(
                id=standalone_id,
                context=MatchContext.STANDALONE,
                status=MatchStatus.PENDING,
                created_by=organizer_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=3,
                is_ranked=True,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    match_id=standalone_id,
                    user_id=organizer_id,
                    position=1,
                ),
                MatchParticipantModel(
                    match_id=standalone_id,
                    user_id=second_id,
                    position=2,
                ),
            ]
        )
        await session.commit()
        stored = await session.get(MatchModel, standalone_id)
        assert stored is not None
        assert stored.game_session_id is None and stored.sequence_number is None

    session_match_id = uuid4()
    async with session_factory() as session:
        session.add(
            MatchModel(
                id=session_match_id,
                context=MatchContext.GAME_SESSION,
                status=MatchStatus.ACTIVE,
                created_by=organizer_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                started_at=datetime.now(UTC),
                game_session_id=session_id,
                sequence_number=1,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    match_id=session_match_id,
                    user_id=organizer_id,
                    position=1,
                ),
                MatchParticipantModel(
                    match_id=session_match_id,
                    user_id=second_id,
                    position=2,
                ),
            ]
        )
        await session.commit()

    second_active_id = uuid4()
    async with session_factory() as session:
        session.add(
            MatchModel(
                id=second_active_id,
                context=MatchContext.GAME_SESSION,
                status=MatchStatus.ACTIVE,
                created_by=organizer_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                started_at=datetime.now(UTC),
                game_session_id=session_id,
                sequence_number=2,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    match_id=second_active_id,
                    user_id=organizer_id,
                    position=1,
                ),
                MatchParticipantModel(
                    match_id=second_active_id,
                    user_id=second_id,
                    position=2,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    duplicate_id = uuid4()
    async with session_factory() as session:
        session.add(
            MatchModel(
                id=duplicate_id,
                context=MatchContext.GAME_SESSION,
                status=MatchStatus.ACTIVE,
                created_by=organizer_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                started_at=datetime.now(UTC),
                game_session_id=session_id,
                sequence_number=1,
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    match_id=duplicate_id,
                    user_id=organizer_id,
                    position=1,
                ),
                MatchParticipantModel(
                    match_id=duplicate_id,
                    user_id=second_id,
                    position=2,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    invalid_id = uuid4()
    async with session_factory() as session:
        session.add(
            MatchModel(
                id=invalid_id,
                context=MatchContext.GAME_SESSION,
                status=MatchStatus.ACTIVE,
                created_by=organizer_id,
                points_to_win=11,
                win_by=2,
                sets_to_win=1,
                is_ranked=True,
                started_at=datetime.now(UTC),
            )
        )
        session.add_all(
            [
                MatchParticipantModel(
                    match_id=invalid_id,
                    user_id=organizer_id,
                    position=1,
                ),
                MatchParticipantModel(
                    match_id=invalid_id,
                    user_id=second_id,
                    position=2,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_current_pair_foreign_keys_distinct_players_and_result_idempotency(
    session_database: tuple[async_sessionmaker[AsyncSession], tuple[UUID, UUID, UUID]],
) -> None:
    session_factory, (organizer_id, second_id, _) = session_database
    session_id = await create_session(session_factory, organizer_id)
    participant_ids = (uuid4(), uuid4())
    async with session_factory() as session:
        session.add_all(
            [
                GameSessionParticipantModel(
                    id=participant_id,
                    session_id=session_id,
                    user_id=user_id,
                )
                for participant_id, user_id in zip(
                    participant_ids,
                    (organizer_id, second_id),
                    strict=True,
                )
            ]
        )
        await session.flush()
        session.add(
            GameSessionCurrentPairModel(
                session_id=session_id,
                player1_participant_id=participant_ids[0],
                player2_participant_id=participant_ids[1],
            )
        )
        result_id = uuid4()
        session.add(GameSessionProcessedResultModel(session_id=session_id, result_id=result_id))
        await session.commit()

    async with session_factory() as session:
        session.add(GameSessionProcessedResultModel(session_id=session_id, result_id=result_id))
        with pytest.raises(IntegrityError):
            await session.commit()

    other_session_id = await create_session(session_factory, organizer_id)
    async with session_factory() as session:
        session.add(
            GameSessionCurrentPairModel(
                session_id=other_session_id,
                player1_participant_id=participant_ids[0],
                player2_participant_id=participant_ids[1],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    lone_participant_id = uuid4()
    async with session_factory() as session:
        session.add(
            GameSessionParticipantModel(
                id=lone_participant_id,
                session_id=other_session_id,
                user_id=organizer_id,
            )
        )
        await session.flush()
        session.add(
            GameSessionCurrentPairModel(
                session_id=other_session_id,
                player1_participant_id=lone_participant_id,
                player2_participant_id=lone_participant_id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
