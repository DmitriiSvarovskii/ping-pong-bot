"""Settings loading tests."""

import logging
from pathlib import Path

from pingstats.core.config import AppEnvironment, Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "PingStats"
    assert settings.app_env is AppEnvironment.DEVELOPMENT
    assert settings.app_debug is True
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.log_level == "INFO"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.db_host == "127.0.0.1"
    assert settings.db_port == 5432
    assert settings.websocket_ticket_ttl_seconds == 30
    assert settings.websocket_heartbeat_interval_seconds == 25
    assert settings.telegram_bot_token.get_secret_value() == ""
    assert settings.telegram_init_data_max_age_seconds == 300


def test_environment_overrides_dotenv(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=From dotenv\nAPP_PORT=9000\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "From environment")

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "From environment"
    assert settings.app_port == 9000


def test_database_url_preserves_hyphenated_name() -> None:
    settings = Settings(
        db_name="Ping-pong-db",
        db_user="postgres",
        db_password="p@ss:/#word",
        _env_file=None,
    )

    url = settings.database_url

    assert url.drivername == "postgresql+asyncpg"
    assert url.database == "Ping-pong-db"
    assert url.username == "postgres"
    assert url.password == "p@ss:/#word"
    assert "p%40ss%3A%2F%23word" in url.render_as_string(hide_password=False)


def test_database_password_is_masked_in_logs(caplog) -> None:
    secret = "never-log-this-password"
    settings = Settings(db_password=secret, _env_file=None)
    logger = logging.getLogger("tests.config")

    with caplog.at_level(logging.INFO):
        logger.info("settings=%s url=%s", settings, settings.database_url)

    assert secret not in caplog.text
    assert "**********" in caplog.text


def test_telegram_bot_token_is_masked_in_settings_repr() -> None:
    secret = "123456789:never-log-this-telegram-token"
    settings = Settings(telegram_bot_token=secret, _env_file=None)

    assert secret not in repr(settings)
    assert "telegram_bot_token=SecretStr('**********')" in repr(settings)
