"""PostgreSQL integration and race tests for matches."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.matches.application.errors import MatchStateError, MatchVersionConflictError
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
        "MatchTest",
        f"match_{user_id.hex}",
        None,
        now,
        now,
        None,
    )


@pytest.fixture
async def match_database() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], UUID, UUID]]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    first_id, second_id = uuid4(), uuid4()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_user(first_id, "First"))
            await users.create(make_user(second_id, "Second"))
            await session.commit()
        yield session_factory, first_id, second_id
        await transaction.rollback()
    await engine.dispose()


async def test_repository_lifecycle_correction_history_and_constraints(
    match_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, creator, invited = match_database
    service = MatchService()
    pending = await service.create_standalone(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        creator_id=creator,
        invited_user_id=invited,
        sets_to_win=2,
    )
    active = await service.accept(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=pending.match.id,
    )
    first = await service.add_set(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=active.match.id,
        score=SetScore(11, 8),
        expected_version=active.match.version,
    )
    completed = await service.add_set(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=first.match.id,
        score=SetScore(12, 10),
        expected_version=first.match.version,
    )
    corrected, revision = await service.correct_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=completed.match.id,
        scores=[SetScore(9, 11), SetScore(8, 11)],
        expected_version=completed.match.version,
        reason="integration correction",
    )

    async with session_factory() as session:
        repository = SqlAlchemyMatchRepository(session)
        revisions, total = await repository.list_revisions(corrected.match.id, limit=10, offset=0)
        listed, listed_total = await repository.list_for_user(
            creator,
            status=MatchStatus.COMPLETED,
            context=None,
            is_ranked=True,
            limit=10,
            offset=0,
        )

    assert corrected.match.winner_id == invited
    assert revision.previous_result.winner_id == creator
    assert revision.new_result.winner_id == invited
    assert total == 1 and revisions[0].reason == "integration correction"
    assert listed_total == 1 and listed[0].match.id == corrected.match.id

    async with session_factory() as session:
        session.add(
            MatchSetModel(
                id=uuid4(),
                match_id=corrected.match.id,
                set_number=1,
                player1_score=11,
                player2_score=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        participant = await session.scalar(
            select(MatchParticipantModel).where(
                MatchParticipantModel.match_id == corrected.match.id
            )
        )
        await session.delete(participant)
        await session.flush()
        with pytest.raises(IntegrityError):
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await session.rollback()


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires temporary committed UUID rows in the local development database",
)
async def test_match_races_are_serialized_and_cleaned_up() -> None:
    database = Database.from_settings(Settings())
    creator, invited = uuid4(), uuid4()
    service = MatchService()
    try:
        async with database.session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_user(creator, "ConcurrentCreator"))
            await users.create(make_user(invited, "ConcurrentInvited"))
            await session.commit()

        response_match = await service.create_standalone(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            creator_id=creator,
            invited_user_id=invited,
        )
        response_results = await asyncio.gather(
            service.accept(
                SqlAlchemyMatchesUnitOfWork(database.session_factory),
                actor_id=invited,
                match_id=response_match.match.id,
            ),
            service.reject(
                SqlAlchemyMatchesUnitOfWork(database.session_factory),
                actor_id=invited,
                match_id=response_match.match.id,
            ),
            return_exceptions=True,
        )
        assert len([item for item in response_results if not isinstance(item, BaseException)]) == 1
        assert len([item for item in response_results if isinstance(item, MatchStateError)]) == 1

        set_match = await service.create_standalone(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            creator_id=creator,
            invited_user_id=invited,
        )
        set_match = await service.accept(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=invited,
            match_id=set_match.match.id,
        )
        set_results = await asyncio.gather(
            *(
                service.add_set(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=actor,
                    match_id=set_match.match.id,
                    score=SetScore(11, 5),
                    expected_version=set_match.match.version,
                )
                for actor in (creator, invited)
            ),
            return_exceptions=True,
        )
        assert len([item for item in set_results if not isinstance(item, BaseException)]) == 1
        assert (
            len([item for item in set_results if isinstance(item, MatchVersionConflictError)]) == 1
        )

        full_match = await service.create_standalone(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            creator_id=creator,
            invited_user_id=invited,
        )
        full_match = await service.accept(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=invited,
            match_id=full_match.match.id,
        )
        scores = [SetScore(11, 1), SetScore(11, 2), SetScore(11, 3)]
        full_results = await asyncio.gather(
            *(
                service.record_full_result(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=actor,
                    match_id=full_match.match.id,
                    scores=scores,
                )
                for actor in (creator, invited)
            )
        )
        assert {item.match.version for item in full_results} == {3}

        correction_version = full_results[0].match.version
        correction_results = await asyncio.gather(
            *(
                service.correct_result(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=actor,
                    match_id=full_match.match.id,
                    scores=[SetScore(1, 11), SetScore(2, 11), SetScore(3, 11)],
                    expected_version=correction_version,
                    reason="concurrent correction",
                )
                for actor in (creator, invited)
            ),
            return_exceptions=True,
        )
        assert (
            len([item for item in correction_results if not isinstance(item, BaseException)]) == 1
        )
        assert (
            len(
                [item for item in correction_results if isinstance(item, MatchVersionConflictError)]
            )
            == 1
        )

        async with database.session_factory() as session:
            set_count = await session.scalar(
                select(func.count())
                .select_from(MatchSetModel)
                .where(MatchSetModel.match_id == full_match.match.id)
            )
            revision_count = await session.scalar(
                select(func.count())
                .select_from(MatchResultRevisionModel)
                .where(MatchResultRevisionModel.match_id == full_match.match.id)
            )
        assert set_count == 3
        assert revision_count == 1
    finally:
        async with database.session_factory() as session:
            match_ids = select(MatchModel.id).where(MatchModel.created_by == creator)
            await session.execute(
                delete(MatchResultRevisionModel).where(
                    MatchResultRevisionModel.match_id.in_(match_ids)
                )
            )
            await session.execute(
                delete(MatchSetModel).where(MatchSetModel.match_id.in_(match_ids))
            )
            await session.execute(
                delete(MatchParticipantModel).where(MatchParticipantModel.match_id.in_(match_ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.id.in_(match_ids)))
            await session.execute(delete(UserModel).where(UserModel.id.in_((creator, invited))))
            await session.commit()
        await database.dispose()
