"""Matches HTTP security and schema contract tests."""

from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.matches.dependencies import get_match_repository, get_matches_uow
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User

SECRET = "test-secret-that-is-at-least-32-characters-long"


class EmptyUserRepository:
    async def get_by_id(self, user_id: UUID) -> User | None:
        del user_id
        return None


async def test_all_match_routes_require_jwt() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = lambda: EmptyUserRepository()
    app.dependency_overrides[get_matches_uow] = lambda: object()
    app.dependency_overrides[get_match_repository] = lambda: object()
    match_id = uuid4()
    complete_sets = {
        "sets": [
            {"player1_score": 11, "player2_score": 1},
            {"player1_score": 11, "player2_score": 2},
            {"player1_score": 11, "player2_score": 3},
        ]
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.post("/api/v1/matches", json={"invited_user_id": str(uuid4())}),
            await client.get("/api/v1/matches"),
            await client.get(f"/api/v1/matches/{match_id}"),
            await client.post(f"/api/v1/matches/{match_id}/accept"),
            await client.post(f"/api/v1/matches/{match_id}/reject"),
            await client.post(f"/api/v1/matches/{match_id}/cancel"),
            await client.post(
                f"/api/v1/matches/{match_id}/sets",
                json={"player1_score": 11, "player2_score": 3, "expected_version": 2},
            ),
            await client.put(f"/api/v1/matches/{match_id}/results", json=complete_sets),
            await client.patch(
                f"/api/v1/matches/{match_id}/results",
                json={**complete_sets, "expected_version": 3},
            ),
            await client.get(f"/api/v1/matches/{match_id}/revisions"),
        ]

    assert {response.status_code for response in responses} == {401}


async def test_score_schema_rejects_non_integer_values() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_matches_uow] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/matches/{uuid4()}/sets",
            json={"player1_score": 11.0, "player2_score": 3, "expected_version": 2},
        )

    assert response.status_code == 422


def test_openapi_contains_all_match_routes() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    paths = app.openapi()["paths"]

    assert {
        "/api/v1/matches",
        "/api/v1/matches/{match_id}",
        "/api/v1/matches/{match_id}/accept",
        "/api/v1/matches/{match_id}/reject",
        "/api/v1/matches/{match_id}/cancel",
        "/api/v1/matches/{match_id}/sets",
        "/api/v1/matches/{match_id}/results",
        "/api/v1/matches/{match_id}/revisions",
    } <= set(paths)
    result_operations = paths["/api/v1/matches/{match_id}/results"]
    assert {"put", "patch"} <= set(result_operations)
