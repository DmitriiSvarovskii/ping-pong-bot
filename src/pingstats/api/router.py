"""Top-level API router."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from pingstats import __version__
from pingstats.core.config import Settings

api_router = APIRouter()


class HealthResponse(BaseModel):
    """Public service health response."""

    status: Literal["ok"]
    service: str
    version: str


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check(request: Request) -> HealthResponse:
    """Report process health without accessing external services."""
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", service=settings.app_name, version=__version__)
