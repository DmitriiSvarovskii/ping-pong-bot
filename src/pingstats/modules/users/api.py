"""Protected users API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.users.application.service import UserNotFoundError, UsersService
from pingstats.modules.users.dependencies import get_user_repository, get_users_service
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository
from pingstats.modules.users.schemas import UserListResponse, UserPublicResponse, UserResponse

users_router = APIRouter(prefix="/users", tags=["users"])


def _collection_response(
    users: list[User] | tuple[User, ...], total: int, limit: int, offset: int
) -> UserListResponse:
    return UserListResponse(
        items=[UserPublicResponse.from_domain(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@users_router.get("/me", response_model=UserResponse)
async def get_own_profile(current_user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.from_domain(current_user)


@users_router.get("", response_model=UserListResponse)
async def list_users(
    _: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)],
    service: Annotated[UsersService, Depends(get_users_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    users, total = await service.list_users(repository, limit=limit, offset=offset)
    return _collection_response(list(users), total, limit, offset)


@users_router.get("/search", response_model=UserListResponse)
async def search_users(
    _: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)],
    service: Annotated[UsersService, Depends(get_users_service)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    users, total = await service.search_users(repository, q, limit=limit, offset=offset)
    return _collection_response(list(users), total, limit, offset)


@users_router.get("/{user_id}", response_model=UserPublicResponse)
async def get_user_profile(
    user_id: UUID,
    _: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)],
    service: Annotated[UsersService, Depends(get_users_service)],
) -> UserPublicResponse:
    try:
        user = await service.get_profile(repository, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    return UserPublicResponse.from_domain(user)
