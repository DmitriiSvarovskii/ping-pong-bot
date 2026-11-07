"""Authentication use cases."""

from dataclasses import dataclass
from uuid import UUID

from pingstats.modules.auth.application.telegram_init_data import TelegramInitDataValidator
from pingstats.modules.auth.application.token_service import TokenService
from pingstats.modules.users.application.ports import UsersUnitOfWork
from pingstats.modules.users.application.service import UsersService
from pingstats.modules.users.domain import User


@dataclass(frozen=True, slots=True)
class LoginResult:
    access_token: str
    token_type: str
    expires_in: int
    user: User


class DevelopmentAuthService:
    """Authenticate the one configured local development identity."""

    def __init__(self, users_service: UsersService, token_service: TokenService) -> None:
        self._users_service = users_service
        self._token_service = token_service

    async def login(self, uow: UsersUnitOfWork, user_id: UUID) -> LoginResult:
        user = await self._users_service.get_or_create_dev_user(uow, user_id)
        return LoginResult(
            access_token=self._token_service.issue_access_token(user.id),
            token_type="bearer",
            expires_in=self._token_service.expires_in,
            user=user,
        )


class TelegramAuthService:
    """Validate Telegram initData, synchronize the profile, and issue the shared JWT."""

    def __init__(
        self,
        validator: TelegramInitDataValidator,
        users_service: UsersService,
        token_service: TokenService,
    ) -> None:
        self._validator = validator
        self._users_service = users_service
        self._token_service = token_service

    async def login(self, uow: UsersUnitOfWork, init_data: str) -> LoginResult:
        telegram_user = self._validator.validate(init_data)
        user = await self._users_service.upsert_telegram_user(uow, telegram_user)
        return LoginResult(
            access_token=self._token_service.issue_access_token(user.id),
            token_type="bearer",
            expires_in=self._token_service.expires_in,
            user=user,
        )
