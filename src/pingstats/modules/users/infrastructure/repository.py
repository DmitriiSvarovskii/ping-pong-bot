"""PostgreSQL users repository."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.users.domain import TelegramUserData, User
from pingstats.modules.users.infrastructure.models import UserModel


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters in a user-provided query."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAlchemyUserRepository:
    """Persist and query users through one request/UoW session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return model.to_domain() if model is not None else None

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        statement = select(UserModel).where(UserModel.telegram_id == telegram_id)
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def create(self, user: User) -> User:
        model = UserModel(
            id=user.id,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            photo_url=user.photo_url,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_active_at=user.last_active_at,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def update(self, user: User) -> User:
        statement = (
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(
                telegram_id=user.telegram_id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                photo_url=user.photo_url,
                updated_at=func.now(),
                last_active_at=user.last_active_at,
            )
            .returning(UserModel)
        )
        model = (await self._session.scalars(statement)).one()
        return model.to_domain()

    async def list_users(self, *, limit: int, offset: int) -> tuple[Sequence[User], int]:
        total = await self._session.scalar(select(func.count()).select_from(UserModel))
        statement = (
            select(UserModel)
            .order_by(UserModel.created_at, UserModel.id)
            .offset(offset)
            .limit(limit)
        )
        models = (await self._session.scalars(statement)).all()
        return tuple(model.to_domain() for model in models), int(total or 0)

    async def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[User], int]:
        escaped = _escape_like(query)
        pattern = f"%{escaped}%"
        predicate = or_(
            UserModel.first_name.ilike(pattern, escape="\\"),
            UserModel.last_name.ilike(pattern, escape="\\"),
            UserModel.username.ilike(pattern, escape="\\"),
        )
        total = await self._session.scalar(
            select(func.count()).select_from(UserModel).where(predicate)
        )
        statement = (
            select(UserModel)
            .where(predicate)
            .order_by(UserModel.created_at, UserModel.id)
            .offset(offset)
            .limit(limit)
        )
        models = (await self._session.scalars(statement)).all()
        return tuple(model.to_domain() for model in models), int(total or 0)

    async def upsert_by_telegram_id(self, data: TelegramUserData) -> User:
        values = {
            "id": uuid4(),
            "telegram_id": data.telegram_id,
            "first_name": data.first_name,
            "last_name": data.last_name,
            "username": data.username,
            "photo_url": data.photo_url,
            "created_at": data.last_active_at,
            "updated_at": data.last_active_at,
            "last_active_at": data.last_active_at,
        }
        statement = (
            insert(UserModel)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[UserModel.telegram_id],
                set_={
                    "first_name": data.first_name,
                    "last_name": data.last_name,
                    "username": data.username,
                    "photo_url": data.photo_url,
                    "updated_at": func.now(),
                    "last_active_at": func.greatest(
                        UserModel.created_at,
                        UserModel.last_active_at,
                        data.last_active_at,
                    ),
                },
            )
            .returning(UserModel)
        )
        model = (await self._session.scalars(statement)).one()
        return model.to_domain()

    async def get_or_create_dev_user(self, user_id: UUID) -> User:
        statement = (
            insert(UserModel)
            .values(
                id=user_id,
                telegram_id=None,
                first_name="Test",
                last_name="Player",
                username="pingstats_dev",
                photo_url=None,
                last_active_at=None,
            )
            .on_conflict_do_nothing(index_elements=[UserModel.id])
            .returning(UserModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        if model is None:
            model = await self._session.get(UserModel, user_id)
        if model is None:
            raise RuntimeError("Development user upsert did not return a user")
        return model.to_domain()
