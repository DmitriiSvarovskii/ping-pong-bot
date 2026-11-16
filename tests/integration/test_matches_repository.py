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
from pingstats.modules.game_sessions.infrastructure.models import GameSessionModel
from pingstats.modules.matches.application.errors import MatchStateError, MatchVersionConflictError
from pingstats.modules.matches.application.service import MatchService
from pingstats.modules.matches.domain import MatchContext, MatchStatus, SetScore
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchResultRevisionModel,
    MatchSetModel,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.matches.infrastructure.uow import SqlAlchemyMatchesUnitOfWork
from pingstats.modules.ratings.application import RatingCorrectionHistoryError
from pingstats.modules.ratings.infrastructure.models import (
    RatingPeriodMatchInputModel,
    RatingPeriodModel,
)
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


class FailingRevisionRepository(SqlAlchemyMatchRepository):
    async def create_revision(self, **kwargs):
        del kwargs
        raise RuntimeError("injected revision failure")


class FailingRevisionUow(SqlAlchemyMatchesUnitOfWork):
    async def __aenter__(self) -> "FailingRevisionUow":
        await super().__aenter__()
        self.matches = FailingRevisionRepository(self.session)
        return self


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


async def test_session_history_paginates_beyond_one_hundred_and_summary_excludes_voided(
    match_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, first_id, second_id = match_database
    now = datetime.now(UTC)
    game_session_id = uuid4()
    async with session_factory() as session:
        session.add(GameSessionModel(id=game_session_id, organizer_id=first_id))
        for sequence_number in range(1, 104):
            if sequence_number <= 101:
                status = MatchStatus.COMPLETED
            elif sequence_number == 102:
                status = MatchStatus.CANCELLED
            else:
                status = MatchStatus.VOIDED
            match_id = uuid4()
            session.add(
                MatchModel(
                    id=match_id,
                    context=MatchContext.GAME_SESSION,
                    status=status,
                    created_by=first_id,
                    points_to_win=11,
                    win_by=2,
                    sets_to_win=1,
                    is_ranked=sequence_number <= 60,
                    winner_id=first_id if status is MatchStatus.COMPLETED else None,
                    game_session_id=game_session_id,
                    sequence_number=sequence_number,
                    started_at=now,
                    completed_at=now if status is not MatchStatus.CANCELLED else None,
                    cancelled_at=now if status is MatchStatus.CANCELLED else None,
                    version=1,
                )
            )
            session.add_all(
                [
                    MatchParticipantModel(
                        id=uuid4(), match_id=match_id, user_id=first_id, position=1
                    ),
                    MatchParticipantModel(
                        id=uuid4(), match_id=match_id, user_id=second_id, position=2
                    ),
                ]
            )
        await session.flush()

        repository = SqlAlchemyMatchRepository(session)
        first_page, total = await repository.list_for_session(game_session_id, limit=100, offset=0)
        second_page, second_total = await repository.list_for_session(
            game_session_id, limit=100, offset=100
        )
        summary = await repository.summarize_session_matches(game_session_id)

    assert len(first_page) == 100
    assert len(second_page) == 3
    assert total == second_total == 103
    assert (summary.played, summary.ranked, summary.friendly) == (101, 60, 41)
    assert len(summary.ranked_matches) == 60


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
        is_ranked=False,
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
        actor_id=creator,
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
            is_ranked=False,
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

    voided, void_revision = await service.void_match(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=corrected.match.id,
        expected_version=corrected.match.version,
        reason="integration void",
    )
    assert voided.match.status is MatchStatus.VOIDED
    assert voided.match.completed_at == completed.match.completed_at
    assert voided.match.winner_id is None
    assert len(voided.sets) == 2
    assert void_revision.revision_number == 2


async def test_revision_failure_rolls_back_match_change(
    match_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, creator, invited = match_database
    service = MatchService()
    pending = await service.create_standalone(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        creator_id=creator,
        invited_user_id=invited,
        is_ranked=False,
    )
    active = await service.accept(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=pending.match.id,
    )
    completed = await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=active.match.id,
        scores=[SetScore(11, 1), SetScore(11, 2), SetScore(11, 3)],
    )
    with pytest.raises(RuntimeError, match="injected revision failure"):
        await service.void_match(
            FailingRevisionUow(session_factory),
            actor_id=creator,
            match_id=completed.match.id,
            expected_version=completed.match.version,
            reason=None,
        )
    async with session_factory() as session:
        stored = await session.get(MatchModel, completed.match.id)
        revision_count = await session.scalar(
            select(func.count())
            .select_from(MatchResultRevisionModel)
            .where(MatchResultRevisionModel.match_id == completed.match.id)
        )
    assert stored is not None and stored.status is MatchStatus.COMPLETED
    assert stored.winner_id == creator
    assert revision_count == 0


async def test_ranked_open_period_correction_and_void_are_versioned_atomically(
    match_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, creator, invited = match_database
    service = MatchService()
    pending = await service.create_standalone(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        creator_id=creator,
        invited_user_id=invited,
        is_ranked=True,
    )
    active = await service.accept(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=pending.match.id,
    )
    completed = await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=active.match.id,
        scores=[SetScore(11, 1), SetScore(11, 2), SetScore(11, 3)],
    )
    async with session_factory() as session:
        initial_period = await session.scalar(
            select(RatingPeriodModel)
            .join(
                RatingPeriodMatchInputModel,
                RatingPeriodMatchInputModel.period_id == RatingPeriodModel.id,
            )
            .where(RatingPeriodMatchInputModel.match_id == completed.match.id)
        )
    assert initial_period is not None
    initial_calculation_version = initial_period.calculation_version
    corrected, revision = await service.correct_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=completed.match.id,
        scores=[SetScore(1, 11), SetScore(2, 11), SetScore(3, 11)],
        expected_version=completed.match.version,
        reason="rated correction",
    )
    async with session_factory() as session:
        period = await session.scalar(
            select(RatingPeriodModel)
            .join(
                RatingPeriodMatchInputModel,
                RatingPeriodMatchInputModel.period_id == RatingPeriodModel.id,
            )
            .where(RatingPeriodMatchInputModel.match_id == completed.match.id)
        )
        inputs = (
            await session.scalars(
                select(RatingPeriodMatchInputModel)
                .where(RatingPeriodMatchInputModel.match_id == completed.match.id)
                .order_by(RatingPeriodMatchInputModel.calculation_version)
            )
        ).all()
    assert period is not None
    assert period.calculation_version == initial_calculation_version + 1
    assert period.calculated_version is None
    assert [item.calculation_version for item in inputs] == [
        initial_calculation_version,
        initial_calculation_version + 1,
    ]
    assert inputs[-1].correction_revision_id == revision.id
    assert inputs[-1].source_match_version == corrected.match.version

    voided, _ = await service.void_match(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=corrected.match.id,
        expected_version=corrected.match.version,
        reason="rated void",
    )
    async with session_factory() as session:
        period = await session.get(RatingPeriodModel, period.id)
        current_input = await session.get(
            RatingPeriodMatchInputModel,
            (period.id, period.calculation_version, completed.match.id),
        )
    assert voided.match.status is MatchStatus.VOIDED
    assert period.calculation_version == initial_calculation_version + 2
    assert current_input is None


async def test_missing_rating_input_rolls_back_match_revision(
    match_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, creator, invited = match_database
    service = MatchService()
    pending = await service.create_standalone(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        creator_id=creator,
        invited_user_id=invited,
        is_ranked=True,
    )
    active = await service.accept(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=invited,
        match_id=pending.match.id,
    )
    completed = await service.record_full_result(
        SqlAlchemyMatchesUnitOfWork(session_factory),
        actor_id=creator,
        match_id=active.match.id,
        scores=[SetScore(11, 1), SetScore(11, 2), SetScore(11, 3)],
    )
    async with session_factory() as session:
        await session.execute(
            delete(RatingPeriodMatchInputModel).where(
                RatingPeriodMatchInputModel.match_id == completed.match.id
            )
        )
        await session.commit()
    with pytest.raises(RatingCorrectionHistoryError):
        await service.correct_result(
            SqlAlchemyMatchesUnitOfWork(session_factory),
            actor_id=creator,
            match_id=completed.match.id,
            scores=[SetScore(1, 11), SetScore(2, 11), SetScore(3, 11)],
            expected_version=completed.match.version,
            reason=None,
        )
    async with session_factory() as session:
        stored = await session.get(MatchModel, completed.match.id)
        revisions = await session.scalar(
            select(func.count())
            .select_from(MatchResultRevisionModel)
            .where(MatchResultRevisionModel.match_id == completed.match.id)
        )
    assert stored is not None and stored.version == completed.match.version
    assert stored.winner_id == completed.match.winner_id
    assert revisions == 0


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
            is_ranked=True,
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
                for actor in (creator, creator)
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
                for actor in (creator, creator)
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
            rated_versions = (
                await session.scalars(
                    select(RatingPeriodMatchInputModel.calculation_version)
                    .where(RatingPeriodMatchInputModel.match_id == full_match.match.id)
                    .order_by(RatingPeriodMatchInputModel.calculation_version)
                )
            ).all()
        assert set_count == 3
        assert revision_count == 1
        assert len(rated_versions) == 2
        assert rated_versions[1] == rated_versions[0] + 1

        race_match = await service.create_standalone(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            creator_id=creator,
            invited_user_id=invited,
            is_ranked=False,
        )
        race_match = await service.accept(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=invited,
            match_id=race_match.match.id,
        )
        race_match = await service.record_full_result(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=creator,
            match_id=race_match.match.id,
            scores=scores,
        )
        correction_vs_void = await asyncio.gather(
            service.correct_result(
                SqlAlchemyMatchesUnitOfWork(database.session_factory),
                actor_id=creator,
                match_id=race_match.match.id,
                scores=[SetScore(1, 11), SetScore(2, 11), SetScore(3, 11)],
                expected_version=race_match.match.version,
                reason="race correction",
            ),
            service.void_match(
                SqlAlchemyMatchesUnitOfWork(database.session_factory),
                actor_id=creator,
                match_id=race_match.match.id,
                expected_version=race_match.match.version,
                reason="race void",
            ),
            return_exceptions=True,
        )
        successful = [item for item in correction_vs_void if not isinstance(item, BaseException)]
        assert len(successful) == 1
        assert len([item for item in correction_vs_void if isinstance(item, BaseException)]) == 1

        void_race = await service.create_standalone(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            creator_id=creator,
            invited_user_id=invited,
            is_ranked=False,
        )
        void_race = await service.accept(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=invited,
            match_id=void_race.match.id,
        )
        void_race = await service.record_full_result(
            SqlAlchemyMatchesUnitOfWork(database.session_factory),
            actor_id=creator,
            match_id=void_race.match.id,
            scores=scores,
        )
        void_results = await asyncio.gather(
            *(
                service.void_match(
                    SqlAlchemyMatchesUnitOfWork(database.session_factory),
                    actor_id=creator,
                    match_id=void_race.match.id,
                    expected_version=void_race.match.version,
                    reason="concurrent void",
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert len([item for item in void_results if not isinstance(item, BaseException)]) == 1
        assert len([item for item in void_results if isinstance(item, BaseException)]) == 1
        async with database.session_factory() as session:
            race_revision_counts = dict(
                (
                    await session.execute(
                        select(
                            MatchResultRevisionModel.match_id,
                            func.count(MatchResultRevisionModel.id),
                        )
                        .where(
                            MatchResultRevisionModel.match_id.in_(
                                (race_match.match.id, void_race.match.id)
                            )
                        )
                        .group_by(MatchResultRevisionModel.match_id)
                    )
                ).all()
            )
        assert race_revision_counts == {race_match.match.id: 1, void_race.match.id: 1}
    finally:
        async with database.session_factory() as session:
            match_ids = select(MatchModel.id).where(MatchModel.created_by == creator)
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
                delete(MatchParticipantModel).where(MatchParticipantModel.match_id.in_(match_ids))
            )
            await session.execute(delete(MatchModel).where(MatchModel.id.in_(match_ids)))
            await session.execute(delete(UserModel).where(UserModel.id.in_((creator, invited))))
            await session.commit()
        await database.dispose()
