"""PostgreSQL friendship repository."""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.friendships.application.ports import FriendRequestItem, FriendshipItem
from pingstats.modules.friendships.domain import FriendRequest, FriendRequestStatus, Friendship
from pingstats.modules.friendships.infrastructure.models import FriendRequestModel, FriendshipModel
from pingstats.modules.users.infrastructure.models import UserModel


class SqlAlchemyFriendshipRepository:
    """Persist friend requests and canonical friendship pairs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]:
        ordered_ids = sorted((first_id, second_id))
        statement = (
            select(UserModel.id)
            .where(UserModel.id.in_(ordered_ids))
            .order_by(UserModel.id)
            .with_for_update()
        )
        return set((await self._session.scalars(statement)).all())

    async def get_request(
        self, request_id: UUID, *, for_update: bool = False
    ) -> FriendRequest | None:
        statement = select(FriendRequestModel).where(FriendRequestModel.id == request_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_request_item(self, request_id: UUID, actor_id: UUID) -> FriendRequestItem | None:
        other_user_id = case(
            (FriendRequestModel.sender_id == actor_id, FriendRequestModel.receiver_id),
            else_=FriendRequestModel.sender_id,
        )
        statement = (
            select(FriendRequestModel, UserModel)
            .join(UserModel, UserModel.id == other_user_id)
            .where(
                FriendRequestModel.id == request_id,
                or_(
                    FriendRequestModel.sender_id == actor_id,
                    FriendRequestModel.receiver_id == actor_id,
                ),
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        request, user = row
        return request.to_domain(), user.to_domain()

    async def create_pending_request(
        self, sender_id: UUID, receiver_id: UUID
    ) -> FriendRequest | None:
        statement = (
            insert(FriendRequestModel)
            .values(
                id=uuid4(),
                sender_id=sender_id,
                receiver_id=receiver_id,
                status=FriendRequestStatus.PENDING,
            )
            .on_conflict_do_nothing()
            .returning(FriendRequestModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def resolve_request(self, request_id: UUID, status: FriendRequestStatus) -> FriendRequest:
        statement = (
            update(FriendRequestModel)
            .where(
                FriendRequestModel.id == request_id,
                FriendRequestModel.status == FriendRequestStatus.PENDING,
            )
            .values(status=status, resolved_at=func.now(), updated_at=func.now())
            .returning(FriendRequestModel)
        )
        return (await self._session.scalars(statement)).one().to_domain()

    async def friendship_exists(self, first_id: UUID, second_id: UUID) -> bool:
        user_id, friend_id = Friendship.canonical_pair(first_id, second_id)
        statement = select(
            select(FriendshipModel.id)
            .where(
                FriendshipModel.user_id == user_id,
                FriendshipModel.friend_id == friend_id,
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def create_friendship(self, first_id: UUID, second_id: UUID) -> Friendship | None:
        user_id, friend_id = Friendship.canonical_pair(first_id, second_id)
        statement = (
            insert(FriendshipModel)
            .values(id=uuid4(), user_id=user_id, friend_id=friend_id)
            .on_conflict_do_nothing(
                index_elements=[FriendshipModel.user_id, FriendshipModel.friend_id]
            )
            .returning(FriendshipModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def delete_friendship(self, first_id: UUID, second_id: UUID) -> bool:
        user_id, friend_id = Friendship.canonical_pair(first_id, second_id)
        statement = (
            delete(FriendshipModel)
            .where(
                FriendshipModel.user_id == user_id,
                FriendshipModel.friend_id == friend_id,
            )
            .returning(FriendshipModel.id)
        )
        return (await self._session.scalar(statement)) is not None

    async def list_incoming(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendRequestItem], int]:
        return await self._list_requests(
            FriendRequestModel.receiver_id == user_id,
            UserModel.id == FriendRequestModel.sender_id,
            limit=limit,
            offset=offset,
        )

    async def list_outgoing(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendRequestItem], int]:
        return await self._list_requests(
            FriendRequestModel.sender_id == user_id,
            UserModel.id == FriendRequestModel.receiver_id,
            limit=limit,
            offset=offset,
        )

    async def _list_requests(
        self, predicate, join_condition, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendRequestItem], int]:
        total = await self._session.scalar(
            select(func.count()).select_from(FriendRequestModel).where(predicate)
        )
        statement = (
            select(FriendRequestModel, UserModel)
            .join(UserModel, join_condition)
            .where(predicate)
            .order_by(FriendRequestModel.created_at.desc(), FriendRequestModel.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple((request.to_domain(), user.to_domain()) for request, user in rows), int(
            total or 0
        )

    async def list_friends(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendshipItem], int]:
        membership = or_(
            FriendshipModel.user_id == user_id,
            FriendshipModel.friend_id == user_id,
        )
        total = await self._session.scalar(
            select(func.count()).select_from(FriendshipModel).where(membership)
        )
        other_user_id = case(
            (FriendshipModel.user_id == user_id, FriendshipModel.friend_id),
            else_=FriendshipModel.user_id,
        )
        statement = (
            select(FriendshipModel, UserModel)
            .join(UserModel, UserModel.id == other_user_id)
            .where(membership)
            .order_by(FriendshipModel.created_at.desc(), FriendshipModel.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple((friendship.to_domain(), user.to_domain()) for friendship, user in rows), int(
            total or 0
        )
