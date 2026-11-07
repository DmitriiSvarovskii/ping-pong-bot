"""Async engine lifecycle owned by the application lifespan."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pingstats.core.config import Settings


@dataclass(frozen=True, slots=True)
class Database:
    """Process-level database resources.

    Creating the engine is lazy with respect to network I/O: PostgreSQL is
    contacted only when a session executes a statement.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        """Create one engine and one session factory for the process."""
        engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        return cls(engine=engine, session_factory=session_factory)

    async def dispose(self) -> None:
        """Release all pooled connections during application shutdown."""
        await self.engine.dispose()
