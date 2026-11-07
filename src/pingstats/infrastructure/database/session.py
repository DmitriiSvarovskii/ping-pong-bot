"""FastAPI dependencies for asynchronous database sessions."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.engine import Database


def get_database(request: Request) -> Database:
    """Resolve process-level database resources from application state."""
    return request.app.state.database


async def get_db_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped session without committing implicitly."""
    async with database.session_factory() as session:
        try:
            yield session
        finally:
            if session.in_transaction():
                await session.rollback()
