"""Authentication application services."""

from pingstats.modules.auth.application.errors import (
    ExpiredTelegramInitDataError,
    InvalidTelegramInitDataError,
    TelegramAuthUnavailableError,
)
from pingstats.modules.auth.application.service import (
    DevelopmentAuthService,
    LoginResult,
    TelegramAuthService,
)
from pingstats.modules.auth.application.telegram_init_data import TelegramInitDataValidator
from pingstats.modules.auth.application.token_service import InvalidAccessTokenError, TokenService

__all__ = [
    "DevelopmentAuthService",
    "ExpiredTelegramInitDataError",
    "InvalidAccessTokenError",
    "InvalidTelegramInitDataError",
    "LoginResult",
    "TelegramAuthService",
    "TelegramAuthUnavailableError",
    "TelegramInitDataValidator",
    "TokenService",
]
