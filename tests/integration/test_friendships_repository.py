"""PostgreSQL integration tests for friendship persistence and concurrency."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.friendships.application.errors import (
    FriendRequestStateError,
    PendingFriendRequestExistsError,
)
from pingstats.modules.friendships.application.service import FriendshipService
from pingstats.modules.friendships.domain import FriendRequestStatus
from pingstats.modules.friendships.infrastructure.models import FriendRequestModel, FriendshipModel
from pingstats.modules.friendships.infrastructure.repository import SqlAlchemyFriendshipRepository
from pingstats.modules.friendships.infrastructure.uow import SqlAlchemyFriendshipsUnitOfWork
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


def make_test_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        telegram_id=None,
        first_name=name,
        last_name="Integration",
        username=f"friendship_{user_id.hex}",
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=None,
    )


@pytest.fixture
async def friendship_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], UUID, UUID]
]:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
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
            await users.create(make_test_user(first_id, "First"))
            await users.create(make_test_user(second_id, "Second"))
            await session.commit()
        yield session_factory, first_id, second_id
        await transaction.rollback()
    await engine.dispose()


async def test_lifecycle_history_lists_and_transactionality(
    friendship_database: tuple[async_sessionmaker[AsyncSession], UUID, UUID],
) -> None:
    session_factory, sender_id, receiver_id = friendship_database
    service = FriendshipService()

    request = await service.send_request(
        SqlAlchemyFriendshipsUnitOfWork(session_factory),
        sender_id=sender_id,
        receiver_id=receiver_id,
    )
    with pytest.raises(PendingFriendRequestExistsError):
        await service.send_request(
            SqlAlchemyFriendshipsUnitOfWork(session_factory),
            sender_id=receiver_id,
            receiver_id=sender_id,
        )

    rollback_uow = SqlAlchemyFriendshipsUnitOfWork(session_factory)
    with pytest.raises(RuntimeError, match="force rollback"):
        async with rollback_uow:
            await rollback_uow.friendships.create_friendship(sender_id, receiver_id)
            raise RuntimeError("force rollback")

    async with session_factory() as session:
        repository = SqlAlchemyFriendshipRepository(session)
        assert not await repository.friendship_exists(sender_id, receiver_id)

    accepted = await service.accept_request(
        SqlAlchemyFriendshipsUnitOfWork(session_factory),
        actor_id=receiver_id,
        request_id=request.id,
    )
    async with session_factory() as session:
        repository = SqlAlchemyFriendshipRepository(session)
        incoming, incoming_total = await repository.list_incoming(receiver_id, limit=10, offset=0)
        friends, friend_total = await repository.list_friends(sender_id, limit=10, offset=0)

    assert accepted.status is FriendRequestStatus.ACCEPTED
    assert incoming_total == 1
    assert incoming[0][1].id == sender_id
    assert friend_total == 1
    assert friends[0][1].id == receiver_id

    await service.remove_friend(
        SqlAlchemyFriendshipsUnitOfWork(session_factory),
        actor_id=sender_id,
        friend_id=receiver_id,
    )
    repeated = await service.send_request(
        SqlAlchemyFriendshipsUnitOfWork(session_factory),
        sender_id=receiver_id,
        receiver_id=sender_id,
    )
    async with session_factory() as session:
        history_count = await session.scalar(select(func.count()).select_from(FriendRequestModel))

    assert repeated.status is FriendRequestStatus.PENDING
    assert history_count == 2


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires explicit permission for temporary committed rows in the local dev database",
)
async def test_concurrent_send_and_accept_are_deterministic() -> None:
    """Use separate connections; clean up only UUIDs created by this test."""
    settings = Settings()
    database = Database.from_settings(settings)
    first_id, second_id = uuid4(), uuid4()
    service = FriendshipService()
    try:
        async with database.session_factory() as session:
            users = SqlAlchemyUserRepository(session)
            await users.create(make_test_user(first_id, "ConcurrentFirst"))
            await users.create(make_test_user(second_id, "ConcurrentSecond"))
            await session.commit()

        send_results = await asyncio.gather(
            service.send_request(
                SqlAlchemyFriendshipsUnitOfWork(database.session_factory),
                sender_id=first_id,
                receiver_id=second_id,
            ),
            service.send_request(
                SqlAlchemyFriendshipsUnitOfWork(database.session_factory),
                sender_id=second_id,
                receiver_id=first_id,
            ),
            return_exceptions=True,
        )
        requests = [item for item in send_results if not isinstance(item, BaseException)]
        conflicts = [item for item in send_results if isinstance(item, BaseException)]
        assert len(requests) == 1
        assert len(conflicts) == 1
        assert isinstance(conflicts[0], PendingFriendRequestExistsError)

        request = requests[0]
        accept_results = await asyncio.gather(
            service.accept_request(
                SqlAlchemyFriendshipsUnitOfWork(database.session_factory),
                actor_id=request.receiver_id,
                request_id=request.id,
            ),
            service.accept_request(
                SqlAlchemyFriendshipsUnitOfWork(database.session_factory),
                actor_id=request.receiver_id,
                request_id=request.id,
            ),
            return_exceptions=True,
        )
        accepted = [item for item in accept_results if not isinstance(item, BaseException)]
        state_conflicts = [item for item in accept_results if isinstance(item, BaseException)]
        assert len(accepted) == 1
        assert len(state_conflicts) == 1
        assert isinstance(state_conflicts[0], FriendRequestStateError)

        async with database.session_factory() as session:
            friendship_count = await session.scalar(
                select(func.count())
                .select_from(FriendshipModel)
                .where(
                    or_(
                        FriendshipModel.user_id.in_((first_id, second_id)),
                        FriendshipModel.friend_id.in_((first_id, second_id)),
                    )
                )
            )
        assert friendship_count == 1
    finally:
        async with database.session_factory() as session:
            await session.execute(
                delete(FriendshipModel).where(
                    or_(
                        FriendshipModel.user_id.in_((first_id, second_id)),
                        FriendshipModel.friend_id.in_((first_id, second_id)),
                    )
                )
            )
            await session.execute(
                delete(FriendRequestModel).where(
                    or_(
                        FriendRequestModel.sender_id.in_((first_id, second_id)),
                        FriendRequestModel.receiver_id.in_((first_id, second_id)),
                    )
                )
            )
            await session.execute(delete(UserModel).where(UserModel.id.in_((first_id, second_id))))
            await session.commit()
        await database.dispose()
