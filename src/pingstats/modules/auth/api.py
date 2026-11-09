"""Telegram Mini App and development-only authentication APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.application import (
    DevelopmentAuthService,
    InvalidRefreshTokenError,
    RefreshAuthenticationError,
    RefreshCsrfError,
    RefreshTokenService,
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
from pingstats.modules.users.dependencies import get_user_repository, get_users_uow
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository
from pingstats.modules.users.infrastructure.uow import SqlAlchemyUsersUnitOfWork

development_auth_router = APIRouter(prefix="/auth", tags=["auth"])
telegram_auth_router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE_NAME = "pingstats_refresh_token"
REFRESH_CSRF_HEADER = "X-PingStats-CSRF"
REFRESH_CSRF_VALUE = "1"


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
    response: Response,
    uow: Annotated[SqlAlchemyUsersUnitOfWork, Depends(get_users_uow)],
) -> TelegramTokenResponse:
    settings: Settings = request.app.state.settings
    bot_token = settings.telegram_bot_token.get_secret_value()
    if not bot_token:
        raise TelegramAuthUnavailableError
    token_service = _access_token_service(settings)
    validator = TelegramInitDataValidator(
        bot_token=bot_token,
        max_age_seconds=settings.telegram_init_data_max_age_seconds,
    )
    result = await TelegramAuthService(validator, UsersService(), token_service).login(
        uow, payload.init_data
    )
    _set_refresh_cookie(response, settings, result.user.id)
    return TelegramTokenResponse.from_result(result)


@telegram_auth_router.post(
    "/refresh",
    response_model=TelegramTokenResponse,
    responses={
        401: {"description": "Missing, invalid, or expired refresh credential"},
        403: {"description": "Missing or invalid refresh CSRF header"},
    },
)
async def refresh_access_token(
    request: Request,
    response: Response,
    repository: Annotated[SqlAlchemyUserRepository, Depends(get_user_repository)],
) -> TelegramTokenResponse:
    settings: Settings = request.app.state.settings
    if request.headers.get(REFRESH_CSRF_HEADER) != REFRESH_CSRF_VALUE:
        raise RefreshCsrfError
    credential = request.cookies.get(REFRESH_COOKIE_NAME)
    try:
        user_id = _refresh_token_service(settings).verify_refresh_token(credential or "")
    except InvalidRefreshTokenError as exc:
        raise RefreshAuthenticationError from exc
    user = await repository.get_by_id(user_id)
    if user is None:
        raise RefreshAuthenticationError
    token_service = _access_token_service(settings)
    response.headers["Cache-Control"] = "no-store"
    return TelegramTokenResponse(
        access_token=token_service.issue_access_token(user.id),
        token_type="bearer",
        expires_in=token_service.expires_in,
    )


@development_auth_router.post("/dev-login", response_model=TokenResponse)
async def development_login(
    request: Request,
    response: Response,
    uow: Annotated[SqlAlchemyUsersUnitOfWork, Depends(get_users_uow)],
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    token_service = _access_token_service(settings)
    result = await DevelopmentAuthService(UsersService(), token_service).login(
        uow,
        settings.dev_user_id,
    )
    _set_refresh_cookie(response, settings, result.user.id)
    return TokenResponse.from_result(result)


def _access_token_service(settings: Settings) -> TokenService:
    return TokenService(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_access_token_expire_minutes,
    )


def _refresh_token_service(settings: Settings) -> RefreshTokenService:
    return RefreshTokenService(
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expire_days=settings.jwt_refresh_token_expire_days,
    )


def _set_refresh_cookie(response: Response, settings: Settings, user_id: UUID) -> None:
    service = _refresh_token_service(settings)
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=service.issue_refresh_token(user_id),
        max_age=service.expires_in,
        httponly=True,
        secure=settings.app_env is AppEnvironment.PRODUCTION,
        samesite="strict",
        path=f"{settings.api_v1_prefix}/auth",
    )
    response.headers["Cache-Control"] = "no-store"
