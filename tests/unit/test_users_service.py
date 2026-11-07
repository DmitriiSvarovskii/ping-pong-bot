"""Users application-service tests."""

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from pingstats.modules.users.application.service import UserNotFoundError, UsersService
from pingstats.modules.users.domain import TelegramUserData, User


def make_user(*, user_id: UUID | None = None, telegram_id: int | None = None) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id or uuid4(),
        telegram_id=telegram_id,
        first_name="Test",
        last_name="Player",
        username="pingstats_dev",
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=None,
    )


class FakeRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = {user.id: user for user in users or []}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return next((u for u in self.users.values() if u.telegram_id == telegram_id), None)

    async def create(self, user: User) -> User:
        self.users[user.id] = user
        return user

    async def update(self, user: User) -> User:
        self.users[user.id] = user
        return user

    async def list_users(self, *, limit: int, offset: int):
        items = list(self.users.values())
        return items[offset : offset + limit], len(items)

    async def search(self, query: str, *, limit: int, offset: int):
        query = query.casefold()
        items = [
            user
            for user in self.users.values()
            if query
            in " ".join(
                part for part in (user.first_name, user.last_name, user.username) if part
            ).casefold()
        ]
        return items[offset : offset + limit], len(items)

    async def upsert_by_telegram_id(self, data: TelegramUserData) -> User:
        user = await self.get_by_telegram_id(data.telegram_id)
        if user is None:
            user = make_user(telegram_id=data.telegram_id)
        user = replace(
            user,
            first_name=data.first_name,
            last_name=data.last_name,
            username=data.username,
            photo_url=data.photo_url,
        )
        self.users[user.id] = user
        return user

    async def get_or_create_dev_user(self, user_id: UUID) -> User:
        user = self.users.get(user_id) or make_user(user_id=user_id)
        self.users[user_id] = user
        return user


class FakeUow:
    def __init__(self, repository: FakeRepository) -> None:
        self.users = repository
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


async def test_get_list_search_and_not_found() -> None:
    user = make_user()
    repository = FakeRepository([user])
    service = UsersService()

    assert await service.get_profile(repository, user.id) == user
    assert await service.list_users(repository, limit=10, offset=0) == ([user], 1)
    assert await service.search_users(repository, " pingstats ", limit=10, offset=0) == (
        [user],
        1,
    )
    with pytest.raises(UserNotFoundError):
        await service.get_profile(repository, uuid4())


async def test_telegram_upsert_is_idempotent() -> None:
    repository = FakeRepository()
    service = UsersService()

    first = await service.upsert_telegram_user(
        FakeUow(repository), TelegramUserData(telegram_id=123, first_name="First")
    )
    second = await service.upsert_telegram_user(
        FakeUow(repository), TelegramUserData(telegram_id=123, first_name="Updated")
    )

    assert first.id == second.id
    assert second.first_name == "Updated"
    assert len(repository.users) == 1


async def test_dev_user_creation_uses_fixed_id() -> None:
    repository = FakeRepository()
    service = UsersService()
    user_id = uuid4()

    first = await service.get_or_create_dev_user(FakeUow(repository), user_id)
    second = await service.get_or_create_dev_user(FakeUow(repository), user_id)

    assert first.id == second.id == user_id
    assert len(repository.users) == 1
