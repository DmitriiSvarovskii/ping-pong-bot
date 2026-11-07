"""Telegram Mini App and development-only authentication APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pingstats.core.config import Settings
from pingstats.modules.auth.application import (
    DevelopmentAuthService,
    TelegramAuthService,
    TelegramAuthUnavailableError,
    TelegramInitDataValidator,
    TokenService,
)
from pingstats.modules.auth.schemas import (
    TelegramLoginRequest,
    TelegramTokenResponse,
    TokenResponse,
)
from pingstats.modules.users.application.service import UsersService
from pingstats.modules.users.dependencies import get_users_uow
from pingstats.modules.users.infrastructure.uow import SqlAlchemyUsersUnitOfWork

development_auth_router = APIRouter(prefix="/auth", tags=["auth"])
telegram_auth_router = APIRouter(prefix="/auth", tags=["auth"])


@telegram_auth_router.post(
    "/telegram",
    response_model=TelegramTokenResponse,
    responses={
        401: {"description": "Invalid, expired, or untrusted Telegram initData"},
        503: {"description": "Telegram authentication is not configured"},
    },
)
async def telegram_login(
    payload: TelegramLoginRequest,
    request: Request,
    uow: Annotated[SqlAlchemyUsersUnitOfWork, Depends(get_users_uow)],
) -> TelegramTokenResponse:
    settings: Settings = request.app.state.settings
    bot_token = settings.telegram_bot_token.get_secret_value()
    if not bot_token:
        raise TelegramAuthUnavailableError
    token_service = TokenService(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_access_token_expire_minutes,
    )
    validator = TelegramInitDataValidator(
        bot_token=bot_token,
        max_age_seconds=settings.telegram_init_data_max_age_seconds,
    )
    result = await TelegramAuthService(validator, UsersService(), token_service).login(
        uow, payload.init_data
    )
    return TelegramTokenResponse.from_result(result)


@development_auth_router.post("/dev-login", response_model=TokenResponse)
async def development_login(
    request: Request,
    uow: Annotated[SqlAlchemyUsersUnitOfWork, Depends(get_users_uow)],
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    token_service = TokenService(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_access_token_expire_minutes,
    )
    result = await DevelopmentAuthService(UsersService(), token_service).login(
        uow,
        settings.dev_user_id,
    )
    return TokenResponse.from_result(result)
