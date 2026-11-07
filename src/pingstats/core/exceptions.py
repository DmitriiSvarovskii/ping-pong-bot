"""Minimal centralized application error handling."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApplicationError(Exception):
    """Base error for expected application failures."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def application_error_handler(_: Request, exc: ApplicationError) -> JSONResponse:
    """Render expected failures without hiding unexpected exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
