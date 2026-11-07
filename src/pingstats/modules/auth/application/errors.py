"""Expected Telegram authentication failures."""

from pingstats.core.exceptions import ApplicationError


class InvalidTelegramInitDataError(ApplicationError):
    def __init__(self, message: str = "Telegram initData is invalid") -> None:
        super().__init__(code="telegram_init_data_invalid", message=message, status_code=401)


class ExpiredTelegramInitDataError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="telegram_init_data_expired",
            message="Telegram initData has expired",
            status_code=401,
        )


class TelegramAuthUnavailableError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="telegram_auth_unavailable",
            message="Telegram authentication is not configured",
            status_code=503,
        )
