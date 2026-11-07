"""Official Telegram Mini App initData validation adapter."""

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from pingstats.modules.auth.application.errors import (
    ExpiredTelegramInitDataError,
    InvalidTelegramInitDataError,
)
from pingstats.modules.users.domain import TelegramUserData

DEFAULT_FUTURE_SKEW_SECONDS = 30
MAX_TELEGRAM_ID = 2**63 - 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TelegramInitDataValidator:
    """Verify Telegram-signed Mini App data before exposing trusted profile fields."""

    def __init__(
        self,
        *,
        bot_token: str,
        max_age_seconds: int,
        future_skew_seconds: int = DEFAULT_FUTURE_SKEW_SECONDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token must not be empty")
        if max_age_seconds <= 0:
            raise ValueError("Telegram initData max age must be positive")
        if future_skew_seconds < 0:
            raise ValueError("Telegram initData future skew must not be negative")
        self._bot_token = bot_token
        self._max_age_seconds = max_age_seconds
        self._future_skew_seconds = future_skew_seconds
        self._clock = clock

    def validate(self, init_data: str) -> TelegramUserData:
        values = self._parse_query(init_data)
        received_hash = values.pop("hash", None)
        if received_hash is None or len(received_hash) != 64:
            raise InvalidTelegramInitDataError

        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret_key = hmac.new(
            b"WebAppData",
            self._bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise InvalidTelegramInitDataError

        now = self._normalized_now()
        auth_date = self._parse_auth_date(values.get("auth_date"))
        age_seconds = now.timestamp() - auth_date
        if age_seconds > self._max_age_seconds:
            raise ExpiredTelegramInitDataError
        if age_seconds < -self._future_skew_seconds:
            raise InvalidTelegramInitDataError("Telegram initData auth_date is in the future")

        return self._parse_user(values.get("user"), last_active_at=now)

    @staticmethod
    def _parse_query(init_data: str) -> dict[str, str]:
        if not init_data:
            raise InvalidTelegramInitDataError
        try:
            pairs = parse_qsl(
                init_data,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeError, ValueError) as error:
            raise InvalidTelegramInitDataError from error
        values: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in values:
                raise InvalidTelegramInitDataError
            values[key] = value
        return values

    @staticmethod
    def _parse_auth_date(raw_value: str | None) -> int:
        try:
            value = int(raw_value or "")
        except ValueError as error:
            raise InvalidTelegramInitDataError from error
        if value <= 0:
            raise InvalidTelegramInitDataError
        return value

    @staticmethod
    def _parse_user(raw_value: str | None, *, last_active_at: datetime) -> TelegramUserData:
        try:
            value = json.loads(raw_value or "")
        except (json.JSONDecodeError, TypeError) as error:
            raise InvalidTelegramInitDataError from error
        if not isinstance(value, dict):
            raise InvalidTelegramInitDataError

        telegram_id = value.get("id")
        first_name = value.get("first_name")
        if (
            isinstance(telegram_id, bool)
            or not isinstance(telegram_id, int)
            or not 0 < telegram_id <= MAX_TELEGRAM_ID
            or not isinstance(first_name, str)
            or not first_name
            or len(first_name) > 128
        ):
            raise InvalidTelegramInitDataError

        optional_fields: dict[str, str | None] = {}
        for key, max_length in (("last_name", 128), ("username", 64), ("photo_url", 2048)):
            item = value.get(key)
            if item is not None and (not isinstance(item, str) or len(item) > max_length):
                raise InvalidTelegramInitDataError
            optional_fields[key] = item

        return TelegramUserData(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=optional_fields["last_name"],
            username=optional_fields["username"],
            photo_url=optional_fields["photo_url"],
            last_active_at=last_active_at,
        )

    def _normalized_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("Telegram auth clock must return a timezone-aware datetime")
        return now.astimezone(UTC)
