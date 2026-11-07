"""Unit of Work transaction-boundary tests."""

from collections.abc import Callable

import pytest

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork


class FakeSession:
    """Small async session test double with transaction state."""

    def __init__(self) -> None:
        self.active_transaction = False
        self.events: list[str] = []

    async def begin(self) -> None:
        self.events.append("begin")
        self.active_transaction = True

    async def commit(self) -> None:
        self.events.append("commit")
        self.active_transaction = False

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.active_transaction = False

    async def close(self) -> None:
        self.events.append("close")

    def in_transaction(self) -> bool:
        return self.active_transaction


def session_factory(session: FakeSession) -> Callable[[], FakeSession]:
    return lambda: session


async def test_uow_opens_and_commits_explicitly() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]

    async with uow:
        assert uow.session is session
        assert session.events == ["begin"]
        await uow.commit()

    assert session.events == ["begin", "commit", "close"]


async def test_uow_rolls_back_on_exception() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="command failed"):
        async with uow:
            raise RuntimeError("command failed")

    assert session.events == ["begin", "rollback", "close"]


async def test_uow_does_not_commit_implicitly() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]

    async with uow:
        pass

    assert session.events == ["begin", "rollback", "close"]


async def test_uow_can_roll_back_explicitly() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]

    async with uow:
        await uow.rollback()

    assert session.events == ["begin", "rollback", "close"]
