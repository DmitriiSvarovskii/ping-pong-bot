"""Users use cases."""

from collections.abc import Sequence
from uuid import UUID

from pingstats.modules.users.application.ports import UserRepository, UsersUnitOfWork
from pingstats.modules.users.domain import TelegramUserData, User


class UserNotFoundError(LookupError):
    """Raised when a requested user does not exist."""


class UsersService:
    """Coordinate user profile use cases without SQL or HTTP concerns."""

    async def get_profile(self, repository: UserRepository, user_id: UUID) -> User:
        user = await repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"User {user_id} was not found")
        return user

    async def list_users(
        self,
        repository: UserRepository,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        return await repository.list_users(limit=limit, offset=offset)

    async def search_users(
        self,
        repository: UserRepository,
        query: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        return await repository.search(query.strip(), limit=limit, offset=offset)

    async def upsert_telegram_user(
        self,
        uow: UsersUnitOfWork,
        data: TelegramUserData,
    ) -> User:
        """Create or update one Telegram identity atomically."""
        async with uow:
            user = await uow.users.upsert_by_telegram_id(data)
            await uow.commit()
        return user

    async def get_or_create_dev_user(
        self,
        uow: UsersUnitOfWork,
        user_id: UUID,
    ) -> User:
        """Create the single configured development user idempotently."""
        async with uow:
            user = await uow.users.get_or_create_dev_user(user_id)
            await uow.commit()
        return user
