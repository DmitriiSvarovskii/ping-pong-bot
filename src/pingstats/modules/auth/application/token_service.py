"""JWT access-token issuing and verification."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from jwt import InvalidTokenError


class InvalidAccessTokenError(ValueError):
    """Raised when an access token cannot be trusted."""


class InvalidRefreshTokenError(ValueError):
    """Raised when a refresh token cannot be trusted."""


class TokenService:
    """Issue and verify signed access tokens independent of Telegram."""

    def __init__(self, *, secret: str, algorithm: str, expire_minutes: int) -> None:
        if algorithm != "HS256":
            raise ValueError("Only HS256 is supported")
        if len(secret) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        self._secret = secret
        self._algorithm = algorithm
        self._lifetime = timedelta(minutes=expire_minutes)

    @property
    def expires_in(self) -> int:
        return int(self._lifetime.total_seconds())

    def issue_access_token(self, user_id: UUID, *, now: datetime | None = None) -> str:
        return _issue_token(
            user_id,
            token_type="access",
            lifetime=self._lifetime,
            secret=self._secret,
            algorithm=self._algorithm,
            now=now,
        )

    def verify_access_token(self, token: str) -> UUID:
        try:
            return _verify_token(
                token,
                expected_type="access",
                secret=self._secret,
                algorithm=self._algorithm,
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidAccessTokenError("Invalid access token") from exc


class RefreshTokenService:
    """Issue an HttpOnly-cookie credential that renews short-lived access tokens."""

    def __init__(self, *, secret: str, algorithm: str, expire_days: int) -> None:
        if algorithm != "HS256":
            raise ValueError("Only HS256 is supported")
        if len(secret) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        if expire_days <= 0:
            raise ValueError("Refresh token lifetime must be positive")
        self._secret = secret
        self._algorithm = algorithm
        self._lifetime = timedelta(days=expire_days)

    @property
    def expires_in(self) -> int:
        return int(self._lifetime.total_seconds())

    def issue_refresh_token(self, user_id: UUID, *, now: datetime | None = None) -> str:
        return _issue_token(
            user_id,
            token_type="refresh",
            lifetime=self._lifetime,
            secret=self._secret,
            algorithm=self._algorithm,
            now=now,
        )

    def verify_refresh_token(self, token: str) -> UUID:
        try:
            return _verify_token(
                token,
                expected_type="refresh",
                secret=self._secret,
                algorithm=self._algorithm,
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRefreshTokenError("Invalid refresh token") from exc


def _issue_token(
    user_id: UUID,
    *,
    token_type: str,
    lifetime: timedelta,
    secret: str,
    algorithm: str,
    now: datetime | None,
) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + lifetime,
        "type": token_type,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def _verify_token(token: str, *, expected_type: str, secret: str, algorithm: str) -> UUID:
    payload = jwt.decode(
        token,
        secret,
        algorithms=[algorithm],
        options={"require": ["sub", "iat", "exp", "type"]},
    )
    if payload["type"] != expected_type:
        raise ValueError("Unexpected token type")
    return UUID(payload["sub"])
