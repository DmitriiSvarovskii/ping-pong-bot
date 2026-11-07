"""Ports required by users application services."""

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pingstats.modules.users.domain import TelegramUserData, User


class UserRepository(Protocol):
    """Persistence operations used by the users application layer."""

    async def get_by_id(self, user_id: UUID) -> User | None: ...

    async def get_by_telegram_id(self, telegram_id: int) -> User | None: ...

    async def create(self, user: User) -> User: ...

    async def update(self, user: User) -> User: ...

    async def list_users(self, *, limit: int, offset: int) -> tuple[Sequence[User], int]: ...

    async def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]: ...

    async def upsert_by_telegram_id(self, data: TelegramUserData) -> User: ...

    async def get_or_create_dev_user(self, user_id: UUID) -> User: ...


class UsersUnitOfWork(Protocol):
    """Transaction boundary exposing the users repository."""

    users: UserRepository

    async def __aenter__(self) -> "UsersUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
