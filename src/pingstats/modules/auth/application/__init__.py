"""Authentication application services."""

from pingstats.modules.auth.application.errors import (
    ExpiredTelegramInitDataError,
    InvalidTelegramInitDataError,
    RefreshAuthenticationError,
    RefreshCsrfError,
    TelegramAuthUnavailableError,
)
from pingstats.modules.auth.application.service import (
    DevelopmentAuthService,
    LoginResult,
    TelegramAuthService,
)
from pingstats.modules.auth.application.telegram_init_data import TelegramInitDataValidator
from pingstats.modules.auth.application.token_service import (
    InvalidAccessTokenError,
    InvalidRefreshTokenError,
    RefreshTokenService,
    TokenService,
)

__all__ = [
    "DevelopmentAuthService",
    "ExpiredTelegramInitDataError",
    "InvalidAccessTokenError",
    "InvalidRefreshTokenError",
    "InvalidTelegramInitDataError",
    "LoginResult",
    "RefreshAuthenticationError",
    "RefreshCsrfError",
    "RefreshTokenService",
    "TelegramAuthService",
    "TelegramAuthUnavailableError",
    "TelegramInitDataValidator",
    "TokenService",
]
