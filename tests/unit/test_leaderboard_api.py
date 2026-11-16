"""HTTP contract tests for the global leaderboard."""

from datetime import UTC, datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.leaderboard.application import RankedRating
from pingstats.modules.leaderboard.dependencies import get_leaderboard_repository
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.dependencies import get_rating_query_service
from tests.unit.test_leaderboard import LeaderboardRepository, make_user
from tests.unit.test_rating_queries import make_period, make_rating


async def test_leaderboard_contract_and_server_pagination() -> None:
    current_user = make_user(uuid4())
    first_user = make_user(uuid4())
    period = make_period()
    repository = LeaderboardRepository(
        (
            RankedRating(
                1,
                first_user,
                make_rating(first_user.id, period),
                period,
            ),
            RankedRating(
                2,
                current_user,
                make_rating(current_user.id, period),
                period,
            ),
        )
    )
    app = create_app(Settings(app_env=AppEnvironment.TESTING, _env_file=None))
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_leaderboard_repository] = lambda: repository
    app.dependency_overrides[get_rating_query_service] = lambda: RatingQueryService(
        clock=lambda: datetime(2026, 11, 14, tzinfo=UTC)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/leaderboard?limit=1&offset=0")
        invalid = await client.get("/api/v1/leaderboard?limit=101")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["current_user_position"] == 2
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["position"] == 1
    assert response.json()["items"][0]["rating"] == "1612.50000"
    assert response.json()["items"][0]["first_name"] == "Rated"
    assert invalid.status_code == 422


async def test_empty_leaderboard_response() -> None:
    current_user = make_user(uuid4())
    app = create_app(Settings(app_env=AppEnvironment.TESTING, _env_file=None))
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_leaderboard_repository] = LeaderboardRepository

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/leaderboard")

    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
        "current_user_position": None,
    }
