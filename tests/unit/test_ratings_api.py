"""HTTP contract tests for published ratings."""

from datetime import UTC, datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.ratings.dependencies import get_rating_query_repository
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User
from tests.unit.test_rating_queries import QueryRepository, make_period, make_rating, make_result

SECRET = "rating-api-test-secret-at-least-32-characters"


def make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        telegram_id=123,
        first_name="Rated",
        last_name="Player",
        username="rated_player",
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=now,
    )


class EmptyUserRepository:
    async def get_by_id(self, user_id):
        del user_id
        return None


async def test_rating_api_requires_authentication() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = EmptyUserRepository

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/ratings/me")

    assert response.status_code == 401


async def test_current_own_player_and_history_contracts() -> None:
    current_user = make_user()
    other_user = make_user()
    period = make_period()
    repository = QueryRepository(
        (make_rating(other_user.id, period), period),
        ((make_result(other_user.id, period), period),),
    )
    app = create_app(Settings(app_env=AppEnvironment.TESTING, _env_file=None))
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_rating_query_repository] = lambda: repository

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own_missing = await client.get("/api/v1/ratings/me")
        current = await client.get(f"/api/v1/ratings/{other_user.id}")
        history = await client.get(f"/api/v1/ratings/{other_user.id}/history?limit=10&offset=0")

    assert own_missing.status_code == 404
    assert own_missing.json()["error"]["code"] == "rating_not_found"
    assert current.status_code == 200
    assert current.json()["user_id"] == str(other_user.id)
    assert current.json()["rating"] == "1612.50000"
    assert "effective_rd" in current.json()
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["period_id"] == str(period.id)


async def test_own_rating_uses_authenticated_user_and_history_can_be_empty() -> None:
    current_user = make_user()
    period = make_period()
    repository = QueryRepository((make_rating(current_user.id, period), period))
    app = create_app(Settings(app_env=AppEnvironment.TESTING, _env_file=None))
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_rating_query_repository] = lambda: repository

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own = await client.get("/api/v1/ratings/me")
        history = await client.get(f"/api/v1/ratings/{uuid4()}/history")
        invalid_page = await client.get(f"/api/v1/ratings/{current_user.id}/history?limit=0")

    assert own.status_code == 200
    assert own.json()["user_id"] == str(current_user.id)
    assert history.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
    assert invalid_page.status_code == 422
