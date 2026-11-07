"""Transactional PostgreSQL tests for the users repository.

Run explicitly with ``RUN_POSTGRES_INTEGRATION=1 uv run pytest tests/integration``.
Every test uses an outer transaction that is rolled back, so existing rows are
never deleted or modified.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.core.config import Settings
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)


@pytest.fixture
async def repository() -> AsyncIterator[SqlAlchemyUserRepository]:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield SqlAlchemyUserRepository(session)
            if session.in_transaction():
                await session.rollback()
        await transaction.rollback()
    await engine.dispose()


def new_user(*, telegram_id: int | None, first_name: str = "Integration") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        telegram_id=telegram_id,
        first_name=first_name,
        last_name="Test",
        username=f"integration_{uuid4().hex}",
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=now,
    )


async def test_create_get_update_search_and_pagination(
    repository: SqlAlchemyUserRepository,
) -> None:
    telegram_id = 8_000_000_000_000_000_000 + int(uuid4().hex[:8], 16)
    created = await repository.create(new_user(telegram_id=telegram_id))

    assert await repository.get_by_id(created.id) == created
    assert await repository.get_by_telegram_id(telegram_id) == created

    updated = await repository.update(replace(created, first_name="Needle%_Name"))
    matches, total = await repository.search("Needle%_Name", limit=10, offset=0)
    page, page_total = await repository.list_users(limit=1, offset=0)

    assert updated.first_name == "Needle%_Name"
    assert [user.id for user in matches] == [created.id]
    assert total == 1
    assert len(page) == 1
    assert page_total >= 1


async def test_telegram_id_is_unique(repository: SqlAlchemyUserRepository) -> None:
    telegram_id = 7_000_000_000_000_000_000 + int(uuid4().hex[:8], 16)
    await repository.create(new_user(telegram_id=telegram_id, first_name="First"))

    with pytest.raises(IntegrityError):
        await repository.create(new_user(telegram_id=telegram_id, first_name="Duplicate"))
