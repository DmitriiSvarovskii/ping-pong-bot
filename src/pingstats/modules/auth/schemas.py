"""HTTP schemas for authentication."""

from typing import Annotated

from pydantic import BaseModel, Field

from pingstats.modules.auth.application import LoginResult
from pingstats.modules.users.schemas import UserPublicResponse


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: UserPublicResponse

    @classmethod
    def from_result(cls, result: LoginResult) -> "TokenResponse":
        return cls(
            access_token=result.access_token,
            token_type=result.token_type,
            expires_in=result.expires_in,
            user=UserPublicResponse.from_domain(result.user),
        )


class TelegramLoginRequest(BaseModel):
    init_data: Annotated[str, Field(min_length=1, max_length=16_384)]


class TelegramTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

    @classmethod
    def from_result(cls, result: LoginResult) -> "TelegramTokenResponse":
        return cls(
            access_token=result.access_token,
            token_type=result.token_type,
            expires_in=result.expires_in,
        )
