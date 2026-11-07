"""Authenticated friend requests and friendships API."""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.friendships.application import FriendshipService
from pingstats.modules.friendships.application.errors import FriendRequestNotFoundError
from pingstats.modules.friendships.application.ports import FriendRequestItem
from pingstats.modules.friendships.dependencies import (
    get_friendship_repository,
    get_friendship_service,
    get_friendships_uow,
)
from pingstats.modules.friendships.infrastructure.repository import SqlAlchemyFriendshipRepository
from pingstats.modules.friendships.infrastructure.uow import SqlAlchemyFriendshipsUnitOfWork
from pingstats.modules.friendships.schemas import (
    CreateFriendRequest,
    FriendRequestListResponse,
    FriendRequestResponse,
    FriendshipListResponse,
    FriendshipResponse,
)
from pingstats.modules.users.domain import User

friends_router = APIRouter(prefix="/friends", tags=["friends"])


def _request_list_response(
    items: Sequence[FriendRequestItem], total: int, limit: int, offset: int
) -> FriendRequestListResponse:
    return FriendRequestListResponse(
        items=[FriendRequestResponse.from_item(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _request_response(
    repository: SqlAlchemyFriendshipRepository,
    request_id: UUID,
    actor_id: UUID,
) -> FriendRequestResponse:
    item = await repository.get_request_item(request_id, actor_id)
    if item is None:
        raise FriendRequestNotFoundError
    return FriendRequestResponse.from_item(item)


@friends_router.post(
    "/requests",
    response_model=FriendRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_friend_request(
    payload: CreateFriendRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyFriendshipsUnitOfWork, Depends(get_friendships_uow)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
) -> FriendRequestResponse:
    request = await service.send_request(
        uow, sender_id=current_user.id, receiver_id=payload.receiver_id
    )
    return await _request_response(repository, request.id, current_user.id)


@friends_router.get("/requests/incoming", response_model=FriendRequestListResponse)
async def list_incoming_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FriendRequestListResponse:
    items, total = await service.list_incoming(
        repository, current_user.id, limit=limit, offset=offset
    )
    return _request_list_response(items, total, limit, offset)


@friends_router.get("/requests/outgoing", response_model=FriendRequestListResponse)
async def list_outgoing_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FriendRequestListResponse:
    items, total = await service.list_outgoing(
        repository, current_user.id, limit=limit, offset=offset
    )
    return _request_list_response(items, total, limit, offset)


async def _resolve_request(
    action: str,
    request_id: UUID,
    current_user: User,
    uow: SqlAlchemyFriendshipsUnitOfWork,
    repository: SqlAlchemyFriendshipRepository,
    service: FriendshipService,
) -> FriendRequestResponse:
    handler = {
        "accept": service.accept_request,
        "reject": service.reject_request,
        "cancel": service.cancel_request,
    }[action]
    request = await handler(uow, actor_id=current_user.id, request_id=request_id)
    return await _request_response(repository, request.id, current_user.id)


@friends_router.post("/requests/{request_id}/accept", response_model=FriendRequestResponse)
async def accept_friend_request(
    request_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyFriendshipsUnitOfWork, Depends(get_friendships_uow)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
) -> FriendRequestResponse:
    return await _resolve_request("accept", request_id, current_user, uow, repository, service)


@friends_router.post("/requests/{request_id}/reject", response_model=FriendRequestResponse)
async def reject_friend_request(
    request_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyFriendshipsUnitOfWork, Depends(get_friendships_uow)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
) -> FriendRequestResponse:
    return await _resolve_request("reject", request_id, current_user, uow, repository, service)


@friends_router.post("/requests/{request_id}/cancel", response_model=FriendRequestResponse)
async def cancel_friend_request(
    request_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyFriendshipsUnitOfWork, Depends(get_friendships_uow)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
) -> FriendRequestResponse:
    return await _resolve_request("cancel", request_id, current_user, uow, repository, service)


@friends_router.get("", response_model=FriendshipListResponse)
async def list_friends(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyFriendshipRepository, Depends(get_friendship_repository)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FriendshipListResponse:
    items, total = await service.list_friends(
        repository, current_user.id, limit=limit, offset=offset
    )
    return FriendshipListResponse(
        items=[FriendshipResponse.from_item(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@friends_router.delete("/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_friend(
    friend_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyFriendshipsUnitOfWork, Depends(get_friendships_uow)],
    service: Annotated[FriendshipService, Depends(get_friendship_service)],
) -> Response:
    await service.remove_friend(uow, actor_id=current_user.id, friend_id=friend_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
