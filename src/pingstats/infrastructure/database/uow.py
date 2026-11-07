"""Explicit transaction boundary for application commands."""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlAlchemyUnitOfWork:
    """Manage exactly one session and transaction per command.

    A successful context exit does not commit automatically. Callers must call
    :meth:`commit`; otherwise the active transaction is rolled back.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._finalized = False

    @property
    def session(self) -> AsyncSession:
        """Return the active session while inside the context manager."""
        if self._session is None:
            raise RuntimeError("Unit of Work is not active")
        return self._session

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        if self._session is not None:
            raise RuntimeError("Unit of Work cannot be entered twice")
        self._session = self._session_factory()
        self._finalized = False
        await self._session.begin()
        return self

    async def commit(self) -> None:
        """Commit explicitly and mark the transaction as finalized."""
        await self.session.commit()
        self._finalized = True

    async def rollback(self) -> None:
        """Roll back explicitly and mark the transaction as finalized."""
        await self.session.rollback()
        self._finalized = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        session = self.session
        try:
            if (exc_type is not None or not self._finalized) and session.in_transaction():
                await session.rollback()
        finally:
            await session.close()
            self._session = None
            self._finalized = False
