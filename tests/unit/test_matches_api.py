"""Matches HTTP security and schema contract tests."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.dependencies import (
    get_match_repository,
    get_match_service,
    get_matches_uow,
)
from pingstats.modules.matches.domain import Match, MatchContext, MatchStatus
from pingstats.modules.ratings.dependencies import get_rating_query_repository
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User
from tests.unit.test_rating_queries import QueryRepository

SECRET = "test-secret-that-is-at-least-32-characters-long"


class EmptyUserRepository:
    async def get_by_id(self, user_id: UUID) -> User | None:
        del user_id
        return None


def make_user() -> User:
    now = datetime.now(UTC)
    return User(uuid4(), None, "Match", "Player", None, None, now, now, None)


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
            await client.post(
                f"/api/v1/matches/{match_id}/void",
                json={"expected_version": 3},
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
    app.dependency_overrides[get_rating_query_repository] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/matches/{uuid4()}/sets",
            json={"player1_score": 11.0, "player2_score": 3, "expected_version": 2},
        )

    assert response.status_code == 422


async def test_standalone_match_responses_contain_all_rating_states() -> None:
    user = make_user()
    now = datetime.now(UTC)
    match = Match(
        id=uuid4(),
        context=MatchContext.STANDALONE,
        status=MatchStatus.COMPLETED,
        created_by=user.id,
        points_to_win=11,
        win_by=2,
        sets_to_win=1,
        is_ranked=True,
        winner_id=user.id,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now,
        cancelled_at=None,
        version=3,
    )
    rated = replace(match, id=uuid4())
    pending = replace(match, id=uuid4())
    voided = replace(
        match,
        id=uuid4(),
        status=MatchStatus.VOIDED,
        winner_id=None,
        version=4,
    )
    session_id = uuid4()
    session_match = replace(
        match,
        id=uuid4(),
        context=MatchContext.GAME_SESSION,
        game_session_id=session_id,
        sequence_number=7,
    )
    views = {
        item.id: MatchView(match=item, participants=(), sets=())
        for item in (match, rated, pending, voided, session_match)
    }

    class MatchService:
        async def get_match(self, repository, **kwargs):
            del repository
            return views[kwargs["match_id"]]

    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_match_repository] = lambda: object()
    app.dependency_overrides[get_match_service] = MatchService
    app.dependency_overrides[get_rating_query_repository] = lambda: QueryRepository(
        published_match_versions={
            match.id: frozenset({2}),
            rated.id: frozenset({rated.version}),
            voided.id: frozenset({voided.version - 1}),
        }
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = {
            match_id: await client.get(f"/api/v1/matches/{match_id}") for match_id in views
        }

    assert {response.status_code for response in responses.values()} == {200}
    states = {match_id: response.json()["rating_state"] for match_id, response in responses.items()}
    assert states == {
        match.id: "recalculating",
        rated.id: "rated",
        pending.id: "pending",
        voided.id: "not_applicable",
        session_match.id: "pending",
    }
    assert responses[session_match.id].json()["game_session_id"] == str(session_id)
    assert responses[session_match.id].json()["sequence_number"] == 7
    assert responses[rated.id].json()["game_session_id"] is None
    assert responses[rated.id].json()["sequence_number"] is None


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
        "/api/v1/matches/{match_id}/void",
    } <= set(paths)
    result_operations = paths["/api/v1/matches/{match_id}/results"]
    assert {"put", "patch"} <= set(result_operations)
