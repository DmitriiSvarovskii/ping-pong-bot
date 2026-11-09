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


class FailingCommitSession(FakeSession):
    async def commit(self) -> None:
        self.events.append("commit_failed")
        raise RuntimeError("commit failed")


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


async def test_after_commit_callbacks_are_deduplicated_and_run_after_transaction() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]
    observed: list[tuple[str, bool]] = []

    async def first() -> None:
        observed.append(("first", session.in_transaction()))

    async def duplicate() -> None:
        observed.append(("duplicate", session.in_transaction()))

    async with uow:
        uow.add_after_commit("session.updated", first)
        uow.add_after_commit("session.updated", duplicate)
        await uow.commit()

    assert observed == [("first", False)]
    assert session.events == ["begin", "commit", "close"]


@pytest.mark.parametrize("explicit", [False, True])
async def test_after_commit_callback_is_discarded_on_rollback(explicit: bool) -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]
    published = False

    async def publish() -> None:
        nonlocal published
        published = True

    async with uow:
        uow.add_after_commit("session.updated", publish)
        if explicit:
            await uow.rollback()

    assert not published


async def test_after_commit_failure_does_not_change_committed_outcome() -> None:
    session = FakeSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]

    async def fail() -> None:
        raise RuntimeError("broadcast failed")

    async with uow:
        uow.add_after_commit("session.updated", fail)
        await uow.commit()

    assert session.events == ["begin", "commit", "close"]


async def test_failed_commit_rolls_back_and_never_runs_callback() -> None:
    session = FailingCommitSession()
    uow = SqlAlchemyUnitOfWork(session_factory(session))  # type: ignore[arg-type]
    published = False

    async def publish() -> None:
        nonlocal published
        published = True

    with pytest.raises(RuntimeError, match="commit failed"):
        async with uow:
            uow.add_after_commit("session.updated", publish)
            await uow.commit()

    assert not published
    assert session.events == ["begin", "commit_failed", "rollback", "close"]
