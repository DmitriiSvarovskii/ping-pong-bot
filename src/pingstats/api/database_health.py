"""Development-only database connectivity health check."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.session import get_db_session

logger = logging.getLogger(__name__)
development_router = APIRouter()


class DatabaseHealthResponse(BaseModel):
    """Safe database connectivity response."""

    status: Literal["ok"]
    database: Literal["connected"]


@development_router.get(
    "/health/db",
    response_model=DatabaseHealthResponse,
    tags=["system"],
)
async def database_health_check(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DatabaseHealthResponse:
    """Execute a minimal query without disclosing connection details."""
    try:
        await session.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as exc:
        logger.warning("Database health check failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return DatabaseHealthResponse(status="ok", database="connected")
