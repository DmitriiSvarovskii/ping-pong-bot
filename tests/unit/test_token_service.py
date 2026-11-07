"""JWT token-service tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from pingstats.modules.auth.application import InvalidAccessTokenError, TokenService

SECRET = "test-secret-that-is-at-least-32-characters-long"


def test_issue_and_verify_access_token() -> None:
    user_id = uuid4()
    service = TokenService(secret=SECRET, algorithm="HS256", expire_minutes=60)

    token = service.issue_access_token(user_id)

    assert service.verify_access_token(token) == user_id
    assert service.expires_in == 3600


@pytest.mark.parametrize(
    ("payload", "secret"),
    [
        (
            {
                "sub": str(uuid4()),
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
                "type": "refresh",
            },
            SECRET,
        ),
        (
            {
                "sub": "not-a-uuid",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
                "type": "access",
            },
            SECRET,
        ),
        (
            {
                "sub": str(uuid4()),
                "iat": datetime.now(UTC) - timedelta(hours=2),
                "exp": datetime.now(UTC) - timedelta(hours=1),
                "type": "access",
            },
            SECRET,
        ),
        (
            {
                "sub": str(uuid4()),
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
                "type": "access",
            },
            "different-secret-that-is-also-long-enough",
        ),
    ],
)
def test_rejects_untrusted_token(payload: dict[str, object], secret: str) -> None:
    service = TokenService(secret=SECRET, algorithm="HS256", expire_minutes=60)
    token = jwt.encode(payload, secret, algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        service.verify_access_token(token)


def test_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        TokenService(secret="too-short", algorithm="HS256", expire_minutes=60)
