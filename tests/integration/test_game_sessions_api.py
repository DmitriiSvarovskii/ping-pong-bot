"""PostgreSQL-backed HTTP acceptance tests for game sessions."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.auth.application import TokenService
from pingstats.modules.game_sessions.dependencies import get_game_sessions_uow
from pingstats.modules.game_sessions.infrastructure.models import (
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyCurrentSessionMatchChecker,
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchResultRevisionModel,
    MatchSetModel,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.users.infrastructure.models import UserModel

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)

SECRET = "e2e-secret-that-is-at-least-32-characters-long"


@dataclass(slots=True)
class ApiEnvironment:
    app: FastAPI
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    users: tuple[UUID, ...]
    tokens: dict[UUID, str]

    def auth(self, user_id: UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[user_id]}"}


async def _cleanup_test_data(
    session_factory: async_sessionmaker[AsyncSession], user_ids: Sequence[UUID]
) -> None:
    async with session_factory() as session:
        session_ids = select(GameSessionModel.id).where(GameSessionModel.organizer_id.in_(user_ids))
        match_ids = select(MatchModel.id).where(MatchModel.game_session_id.in_(session_ids))
        await session.execute(
            delete(MatchResultRevisionModel).where(MatchResultRevisionModel.match_id.in_(match_ids))
        )
        await session.execute(delete(MatchSetModel).where(MatchSetModel.match_id.in_(match_ids)))
        await session.execute(
            delete(MatchParticipantModel).where(MatchParticipantModel.match_id.in_(match_ids))
        )
        await session.execute(delete(MatchModel).where(MatchModel.id.in_(match_ids)))
        await session.execute(
            delete(GameSessionProcessedResultModel).where(
                GameSessionProcessedResultModel.session_id.in_(session_ids)
            )
        )
        await session.execute(
            delete(GameSessionQueueEventModel).where(
                GameSessionQueueEventModel.session_id.in_(session_ids)
            )
        )
        await session.execute(
            delete(GameSessionQueueModel).where(GameSessionQueueModel.session_id.in_(session_ids))
        )
        await session.execute(
            delete(GameSessionCurrentPairModel).where(
                GameSessionCurrentPairModel.session_id.in_(session_ids)
            )
        )
        await session.execute(
            delete(GameSessionParticipantModel).where(
                GameSessionParticipantModel.session_id.in_(session_ids)
            )
        )
        await session.execute(delete(GameSessionModel).where(GameSessionModel.id.in_(session_ids)))
        await session.execute(delete(UserModel).where(UserModel.id.in_(user_ids)))
        await session.commit()


@pytest.fixture
async def game_sessions_api_environment() -> AsyncIterator[ApiEnvironment]:
    database_settings = Settings()
    engine = create_async_engine(database_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    users = tuple(uuid4() for _ in range(8))
    async with session_factory() as session:
        session.add_all(
            [
                UserModel(
                    id=user_id,
                    first_name=f"E2E {position}",
                    username=f"e2e_{user_id.hex}",
                )
                for position, user_id in enumerate(users, start=1)
            ]
        )
        await session.commit()

    settings = Settings(
        app_env=AppEnvironment.TESTING,
        app_debug=False,
        jwt_secret_key=SECRET,
        _env_file=None,
    )
    app = create_app(settings)
    app.state.database = Database(engine=engine, session_factory=session_factory)
    token_service = TokenService(secret=SECRET, algorithm="HS256", expire_minutes=60)
    tokens = {user_id: token_service.issue_access_token(user_id) for user_id in users}
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield ApiEnvironment(app, client, session_factory, users, tokens)
    finally:
        await client.aclose()
        await _cleanup_test_data(session_factory, users)
        await engine.dispose()


async def _create_session(
    env: ApiEnvironment, organizer: UUID, *, name: str = "E2E session"
) -> UUID:
    response = await env.client.post(
        "/api/v1/game-sessions",
        headers=env.auth(organizer),
        json={"name": name, "points_to_win": 11, "sets_to_win": 1, "is_ranked": True},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _join(env: ApiEnvironment, session_id: UUID, user_id: UUID) -> Response:
    return await env.client.post(
        f"/api/v1/game-sessions/{session_id}/join", headers=env.auth(user_id)
    )


async def _start(env: ApiEnvironment, session_id: UUID, organizer: UUID) -> Response:
    return await env.client.post(
        f"/api/v1/game-sessions/{session_id}/start", headers=env.auth(organizer)
    )


async def _current(env: ApiEnvironment, session_id: UUID, user_id: UUID) -> dict:
    response = await env.client.get(
        f"/api/v1/game-sessions/{session_id}/matches/current", headers=env.auth(user_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _record(
    env: ApiEnvironment,
    session_id: UUID,
    actor: UUID,
    match_id: str,
    first_score: int,
    second_score: int,
) -> Response:
    return await env.client.put(
        f"/api/v1/game-sessions/{session_id}/matches/current/result",
        headers=env.auth(actor),
        json={
            "match_id": match_id,
            "sets": [{"player1_score": first_score, "player2_score": second_score}],
        },
    )


def _queue_user_ids(payload: dict, key: str) -> list[str]:
    return [item["user_id"] for item in payload[key]]


async def _assert_match_invariants(env: ApiEnvironment, session_id: UUID) -> None:
    async with env.session_factory() as session:
        active_count = await session.scalar(
            select(func.count())
            .select_from(MatchModel)
            .where(
                MatchModel.game_session_id == session_id,
                MatchModel.status == MatchStatus.ACTIVE,
            )
        )
        total = await session.scalar(
            select(func.count())
            .select_from(MatchModel)
            .where(MatchModel.game_session_id == session_id)
        )
        distinct_sequences = await session.scalar(
            select(func.count(func.distinct(MatchModel.sequence_number))).where(
                MatchModel.game_session_id == session_id
            )
        )
    assert int(active_count or 0) <= 1
    assert int(total or 0) == int(distinct_sequences or 0)


async def test_three_player_http_acceptance_flow(
    game_sessions_api_environment: ApiEnvironment,
) -> None:
    env = game_sessions_api_environment
    player_a, player_b, player_c = env.users[:3]
    session_id = await _create_session(env, player_a)
    assert (await _join(env, session_id, player_b)).status_code == 200
    assert (await _join(env, session_id, player_c)).status_code == 200
    assert (await _start(env, session_id, player_a)).status_code == 200

    queue = (
        await env.client.get(
            f"/api/v1/game-sessions/{session_id}/queue", headers=env.auth(player_a)
        )
    ).json()
    assert _queue_user_ids(queue, "current_pair") == [str(player_a), str(player_b)]
    assert _queue_user_ids(queue, "waiting") == [str(player_c)]
    first = await _current(env, session_id, player_a)
    assert first["sequence_number"] == 1 and first["status"] == "active"

    first_result = await _record(env, session_id, player_a, first["id"], 11, 8)
    assert first_result.status_code == 200, first_result.text
    assert first_result.json()["match"]["winner_id"] == str(player_a)
    queue = first_result.json()["queue"]
    assert _queue_user_ids(queue, "current_pair") == [str(player_a), str(player_c)]
    assert _queue_user_ids(queue, "waiting") == [str(player_b)]
    second = first_result.json()["current_match"]
    assert second["sequence_number"] == 2

    second_result = await _record(env, session_id, player_c, second["id"], 8, 11)
    assert second_result.status_code == 200, second_result.text
    queue = second_result.json()["queue"]
    assert _queue_user_ids(queue, "current_pair") == [str(player_c), str(player_b)]
    assert _queue_user_ids(queue, "waiting") == [str(player_a)]
    assert second_result.json()["current_match"]["sequence_number"] == 3

    history = await env.client.get(
        f"/api/v1/game-sessions/{session_id}/matches", headers=env.auth(player_b)
    )
    assert history.status_code == 200
    assert [item["sequence_number"] for item in history.json()["items"]] == [1, 2, 3]
    assert [item["status"] for item in history.json()["items"]] == [
        "completed",
        "completed",
        "active",
    ]

    completed = await env.client.post(
        f"/api/v1/game-sessions/{session_id}/complete", headers=env.auth(player_a)
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    no_current = await env.client.get(
        f"/api/v1/game-sessions/{session_id}/matches/current",
        headers=env.auth(player_a),
    )
    assert no_current.status_code == 404
    preserved = await env.client.get(
        f"/api/v1/game-sessions/{session_id}/matches", headers=env.auth(player_a)
    )
    assert [item["status"] for item in preserved.json()["items"]] == [
        "completed",
        "completed",
        "cancelled",
    ]
    assert [item["sets"] for item in preserved.json()["items"][:2]]
    await _assert_match_invariants(env, session_id)


async def test_cancel_leave_pause_resume_and_pending_cancel(
    game_sessions_api_environment: ApiEnvironment,
) -> None:
    env = game_sessions_api_environment
    organizer, player, replacement, waiting = env.users[:4]
    session_id = await _create_session(env, organizer, name="Cancel flow")
    assert (await _join(env, session_id, player)).status_code == 200
    assert (await _start(env, session_id, organizer)).status_code == 200
    first = await _current(env, session_id, player)

    cancelled = await env.client.post(
        f"/api/v1/game-sessions/{session_id}/matches/current/cancel",
        headers=env.auth(player),
        json={"match_id": first["id"]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["current_match"]["sequence_number"] == 2
    assert cancelled.json()["queue"]["waiting"] == []

    second_id = cancelled.json()["current_match"]["id"]
    paused = await env.client.post(
        f"/api/v1/game-sessions/{session_id}/matches/current/cancel-and-leave",
        headers=env.auth(player),
        json={"match_id": second_id},
    )
    assert paused.status_code == 200
    assert paused.json()["current_match"] is None and paused.json()["queue"] is None
    queue = await env.client.get(
        f"/api/v1/game-sessions/{session_id}/queue", headers=env.auth(organizer)
    )
    assert queue.json() == {"current_pair": [], "waiting": [], "is_paused": True}

    resumed = await _join(env, session_id, replacement)
    assert resumed.status_code == 200
    current = await _current(env, session_id, organizer)
    assert current["sequence_number"] == 3
    assert {item["user"]["id"] for item in current["participants"]} == {
        str(organizer),
        str(replacement),
    }

    joined = await _join(env, session_id, waiting)
    assert joined.status_code == 200
    left = await env.client.post(
        f"/api/v1/game-sessions/{session_id}/leave", headers=env.auth(waiting)
    )
    assert left.status_code == 200
    playing_leave = await env.client.post(
        f"/api/v1/game-sessions/{session_id}/leave", headers=env.auth(replacement)
    )
    assert playing_leave.status_code == 409

    pending_id = await _create_session(env, replacement, name="Pending cancellation")
    forbidden = await env.client.post(
        f"/api/v1/game-sessions/{pending_id}/cancel", headers=env.auth(organizer)
    )
    assert forbidden.status_code == 403
    cancelled_pending = await env.client.post(
        f"/api/v1/game-sessions/{pending_id}/cancel", headers=env.auth(replacement)
    )
    assert cancelled_pending.status_code == 200
    assert cancelled_pending.json()["status"] == "cancelled"


async def test_idempotency_stale_score_permissions_and_completed_guards(
    game_sessions_api_environment: ApiEnvironment,
) -> None:
    env = game_sessions_api_environment
    organizer, player, outsider = env.users[:3]

    unauthenticated = await env.client.get("/api/v1/game-sessions")
    invalid_token = await env.client.get(
        "/api/v1/game-sessions", headers={"Authorization": "Bearer invalid"}
    )
    assert unauthenticated.status_code == invalid_token.status_code == 401

    pending_id = await _create_session(env, organizer, name="Permissions pending")
    assert (
        await env.client.post(
            f"/api/v1/game-sessions/{pending_id}/start", headers=env.auth(outsider)
        )
    ).status_code == 403
    assert (
        await env.client.post(
            f"/api/v1/game-sessions/{pending_id}/cancel", headers=env.auth(outsider)
        )
    ).status_code == 403

    assert (await _join(env, pending_id, player)).status_code == 200
    assert (await _start(env, pending_id, organizer)).status_code == 200
    first = await _current(env, pending_id, organizer)
    assert (
        await env.client.post(
            f"/api/v1/game-sessions/{pending_id}/complete", headers=env.auth(outsider)
        )
    ).status_code == 403
    outsider_result = await _record(env, pending_id, outsider, first["id"], 11, 8)
    assert outsider_result.status_code == 403
    outsider_history = await env.client.get(
        f"/api/v1/game-sessions/{pending_id}/matches", headers=env.auth(outsider)
    )
    assert outsider_history.status_code == 403

    invalid_score = await _record(env, pending_id, organizer, first["id"], 11, 10)
    assert invalid_score.status_code == 400
    result = await _record(env, pending_id, organizer, first["id"], 11, 8)
    assert result.status_code == 200
    repeated = await _record(env, pending_id, player, first["id"], 11, 8)
    assert repeated.status_code == 200
    assert repeated.json()["current_match"]["id"] == result.json()["current_match"]["id"]
    stale = await _record(env, pending_id, organizer, first["id"], 11, 7)
    assert stale.status_code == 409

    correction = await env.client.patch(
        f"/api/v1/matches/{first['id']}/results",
        headers=env.auth(organizer),
        json={
            "sets": [{"player1_score": 11, "player2_score": 6}],
            "expected_version": result.json()["match"]["version"],
        },
    )
    assert correction.status_code == 409

    completed = await env.client.post(
        f"/api/v1/game-sessions/{pending_id}/complete", headers=env.auth(organizer)
    )
    assert completed.status_code == 200
    assert (await _join(env, pending_id, outsider)).status_code == 409
    assert (
        await env.client.post(
            f"/api/v1/game-sessions/{pending_id}/start", headers=env.auth(organizer)
        )
    ).status_code == 409
    successor_id = result.json()["current_match"]["id"]
    assert (await _record(env, pending_id, organizer, successor_id, 11, 5)).status_code == 409


async def _new_started_session(
    env: ApiEnvironment, players: Sequence[UUID], *, name: str
) -> tuple[UUID, dict]:
    session_id = await _create_session(env, players[0], name=name)
    for player in players[1:]:
        assert (await _join(env, session_id, player)).status_code == 200
    assert (await _start(env, session_id, players[0])).status_code == 200
    return session_id, await _current(env, session_id, players[0])


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires explicit permission for committed concurrent PostgreSQL rows",
)
async def test_http_concurrency_preserves_session_invariants(
    game_sessions_api_environment: ApiEnvironment,
) -> None:
    env = game_sessions_api_environment
    a, b, c, d = env.users[:4]

    duplicate_id, duplicate_match = await _new_started_session(
        env, (a, b, c), name="Duplicate result"
    )
    duplicate_responses = await asyncio.gather(
        _record(env, duplicate_id, a, duplicate_match["id"], 11, 8),
        _record(env, duplicate_id, b, duplicate_match["id"], 11, 8),
    )
    assert [response.status_code for response in duplicate_responses] == [200, 200]
    duplicate_history = await env.client.get(
        f"/api/v1/game-sessions/{duplicate_id}/matches", headers=env.auth(a)
    )
    assert duplicate_history.json()["total"] == 2
    await _assert_match_invariants(env, duplicate_id)

    cancel_id, cancel_match = await _new_started_session(env, (a, b, c), name="Result cancel")
    result_vs_cancel = await asyncio.gather(
        _record(env, cancel_id, a, cancel_match["id"], 11, 7),
        env.client.post(
            f"/api/v1/game-sessions/{cancel_id}/matches/current/cancel",
            headers=env.auth(b),
            json={"match_id": cancel_match["id"]},
        ),
    )
    assert all(response.status_code in {200, 409} for response in result_vs_cancel)
    assert 200 in {response.status_code for response in result_vs_cancel}
    await _assert_match_invariants(env, cancel_id)

    complete_id, complete_match = await _new_started_session(env, (a, b, c), name="Result complete")
    result_vs_complete = await asyncio.gather(
        _record(env, complete_id, a, complete_match["id"], 11, 6),
        env.client.post(f"/api/v1/game-sessions/{complete_id}/complete", headers=env.auth(a)),
    )
    assert all(response.status_code in {200, 409} for response in result_vs_complete)
    assert 500 not in {response.status_code for response in result_vs_complete}
    await _assert_match_invariants(env, complete_id)

    join_id, join_match = await _new_started_session(env, (a, b, c), name="Join result")
    join_vs_result = await asyncio.gather(
        _join(env, join_id, d),
        _record(env, join_id, a, join_match["id"], 11, 5),
    )
    assert [response.status_code for response in join_vs_result] == [200, 200]
    await _assert_match_invariants(env, join_id)

    leave_id, leave_match = await _new_started_session(env, (a, b, c, d), name="Leave rotation")
    leave_vs_rotation = await asyncio.gather(
        env.client.post(f"/api/v1/game-sessions/{leave_id}/leave", headers=env.auth(c)),
        _record(env, leave_id, a, leave_match["id"], 11, 4),
    )
    assert all(response.status_code in {200, 409} for response in leave_vs_rotation)
    assert 500 not in {response.status_code for response in leave_vs_rotation}
    participants = await env.client.get(
        f"/api/v1/game-sessions/{leave_id}/participants?limit=100",
        headers=env.auth(a),
    )
    queue = await env.client.get(f"/api/v1/game-sessions/{leave_id}/queue", headers=env.auth(a))
    represented = len(queue.json()["current_pair"]) + len(queue.json()["waiting"])
    assert represented == participants.json()["total"]
    await _assert_match_invariants(env, leave_id)


class FailingSuccessorMatchRepository(SqlAlchemyMatchRepository):
    async def create_game_session_match(self, **kwargs):
        if kwargs["sequence_number"] > 1:
            raise RuntimeError("injected successor creation failure")
        return await super().create_game_session_match(**kwargs)


class FailingSuccessorUow(SqlAlchemyUnitOfWork):
    async def __aenter__(self) -> "FailingSuccessorUow":
        await super().__aenter__()
        self.game_sessions = SqlAlchemyGameSessionRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = FailingSuccessorMatchRepository(self.session)
        return self


async def test_http_successor_failure_rolls_back_entire_result(
    game_sessions_api_environment: ApiEnvironment,
) -> None:
    env = game_sessions_api_environment
    organizer, player, waiting = env.users[:3]
    session_id, current = await _new_started_session(
        env, (organizer, player, waiting), name="Rollback"
    )
    queue_before = (
        await env.client.get(
            f"/api/v1/game-sessions/{session_id}/queue", headers=env.auth(organizer)
        )
    ).json()
    async with env.session_factory() as session:
        events_before = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueEventModel)
            .where(GameSessionQueueEventModel.session_id == session_id)
        )

    env.app.dependency_overrides[get_game_sessions_uow] = lambda: FailingSuccessorUow(
        env.session_factory
    )
    failing_client = AsyncClient(
        transport=ASGITransport(app=env.app, raise_app_exceptions=False),
        base_url="http://test",
    )
    try:
        failed = await failing_client.put(
            f"/api/v1/game-sessions/{session_id}/matches/current/result",
            headers=env.auth(organizer),
            json={
                "match_id": current["id"],
                "sets": [{"player1_score": 11, "player2_score": 8}],
            },
        )
    finally:
        await failing_client.aclose()
        env.app.dependency_overrides.pop(get_game_sessions_uow, None)
    assert failed.status_code == 500
    assert failed.text == "Internal Server Error"

    current_after = await _current(env, session_id, organizer)
    assert current_after["id"] == current["id"]
    assert current_after["status"] == "active" and current_after["sets"] == []
    queue_after = (
        await env.client.get(
            f"/api/v1/game-sessions/{session_id}/queue", headers=env.auth(organizer)
        )
    ).json()
    assert queue_after == queue_before

    async with env.session_factory() as session:
        match_count = await session.scalar(
            select(func.count())
            .select_from(MatchModel)
            .where(MatchModel.game_session_id == session_id)
        )
        set_count = await session.scalar(
            select(func.count())
            .select_from(MatchSetModel)
            .join(MatchModel, MatchModel.id == MatchSetModel.match_id)
            .where(MatchModel.game_session_id == session_id)
        )
        processed_count = await session.scalar(
            select(func.count())
            .select_from(GameSessionProcessedResultModel)
            .where(GameSessionProcessedResultModel.session_id == session_id)
        )
        events_after = await session.scalar(
            select(func.count())
            .select_from(GameSessionQueueEventModel)
            .where(GameSessionQueueEventModel.session_id == session_id)
        )
    assert (match_count, set_count, processed_count) == (1, 0, 0)
    assert events_after == events_before
