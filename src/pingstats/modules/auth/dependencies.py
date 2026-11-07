"""Shared authentication dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pingstats.core.config import Settings
from pingstats.modules.auth.application import InvalidAccessTokenError, TokenService
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

bearer_scheme = HTTPBearer(auto_error=False)


def get_token_service(request: Request) -> TokenService:
    settings: Settings = request.app.state.settings
    return TokenService(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_access_token_expire_minutes,
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    repository: Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        user_id = token_service.verify_access_token(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise unauthorized from exc
    user = await repository.get_by_id(user_id)
    if user is None:
        raise unauthorized
    return user
