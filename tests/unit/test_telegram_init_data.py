"""Telegram Mini App initData validation tests."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from pingstats.modules.auth.application import (
    ExpiredTelegramInitDataError,
    InvalidTelegramInitDataError,
    TelegramInitDataValidator,
)

BOT_TOKEN = "123456789:test-bot-token-used-only-in-tests"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def signed_init_data(
    *,
    user: dict[str, object] | str | None = None,
    auth_date: int | None = None,
    bot_token: str = BOT_TOKEN,
    overrides: dict[str, str] | None = None,
) -> str:
    raw_user = (
        json.dumps(
            user
            or {
                "id": 987654321,
                "first_name": "Alice",
                "last_name": "Player",
                "username": "alice",
                "photo_url": "https://example.test/alice.jpg",
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if not isinstance(user, str)
        else user
    )
    values = {
        "auth_date": str(auth_date or int(NOW.timestamp())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": raw_user,
    }
    values.update(overrides or {})
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def validator(**kwargs) -> TelegramInitDataValidator:
    return TelegramInitDataValidator(
        bot_token=BOT_TOKEN,
        max_age_seconds=300,
        clock=lambda: NOW,
        **kwargs,
    )


def replace_query_value(init_data: str, old: str, new: str) -> str:
    assert old in init_data
    return init_data.replace(old, new, 1)


def test_valid_init_data_returns_only_signed_profile() -> None:
    result = validator().validate(signed_init_data())

    assert result.telegram_id == 987654321
    assert result.first_name == "Alice"
    assert result.last_name == "Player"
    assert result.username == "alice"
    assert result.photo_url == "https://example.test/alice.jpg"
    assert result.last_active_at == NOW


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("987654321", "987654322"),
        (str(int(NOW.timestamp())), str(int(NOW.timestamp()) - 1)),
        ("alice", "mallory"),
    ],
)
def test_tampered_signed_fields_are_rejected(original: str, replacement: str) -> None:
    tampered = replace_query_value(signed_init_data(), original, replacement)

    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(tampered)


def test_wrong_or_missing_hash_is_rejected() -> None:
    valid = signed_init_data()
    wrong = valid.rsplit("=", 1)[0] + "=" + "0" * 64
    missing = "&".join(item for item in valid.split("&") if not item.startswith("hash="))

    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(wrong)
    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(missing)


def test_expired_and_excessively_future_auth_date_are_rejected() -> None:
    expired = signed_init_data(auth_date=int((NOW - timedelta(seconds=301)).timestamp()))
    future = signed_init_data(auth_date=int((NOW + timedelta(seconds=31)).timestamp()))

    with pytest.raises(ExpiredTelegramInitDataError):
        validator().validate(expired)
    with pytest.raises(InvalidTelegramInitDataError, match="future"):
        validator().validate(future)


@pytest.mark.parametrize(
    "raw_user",
    [
        "not-json",
        "[]",
        "{}",
        '{"id":true,"first_name":"Alice"}',
        '{"id":123,"first_name":""}',
        '{"id":123,"first_name":"Alice","username":42}',
    ],
)
def test_invalid_user_json_or_shape_is_rejected(raw_user: str) -> None:
    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(signed_init_data(user=raw_user))


def test_duplicate_fields_and_wrong_bot_token_are_rejected() -> None:
    valid = signed_init_data()

    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(f"auth_date=1&{valid}")
    with pytest.raises(InvalidTelegramInitDataError):
        validator().validate(signed_init_data(bot_token="different-token"))


def test_validator_configuration_is_strict() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        TelegramInitDataValidator(bot_token="", max_age_seconds=300)
    with pytest.raises(ValueError, match="must be positive"):
        TelegramInitDataValidator(bot_token=BOT_TOKEN, max_age_seconds=0)
