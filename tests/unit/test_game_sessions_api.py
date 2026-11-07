"""Game sessions HTTP security and contract tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.game_sessions.application.dto import (
    GameSessionMatchResult,
    GameSessionParticipantItem,
    GameSessionQueueView,
    GameSessionView,
)
from pingstats.modules.game_sessions.application.errors import (
    GameSessionNotFoundError,
    GameSessionPermissionError,
)
from pingstats.modules.game_sessions.dependencies import (
    get_game_session_match_repository,
    get_game_session_repository,
    get_game_session_service,
    get_game_sessions_uow,
)
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionParticipant,
    GameSessionStatus,
)
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import InvalidScoreError, MatchStateError
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchSet,
    MatchStatus,
)
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User

SECRET = "test-secret-that-is-at-least-32-characters-long"


class EmptyUserRepository:
    async def get_by_id(self, user_id: UUID) -> User | None:
        del user_id
        return None


def make_user(name: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        telegram_id=None,
        first_name=name,
        last_name="Player",
        username=name.lower(),
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=None,
    )


class SessionApiFixture:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)
        self.organizer = make_user("Alice")
        self.opponent = make_user("Bob")
        self.session_id = uuid4()
        self.session = GameSession(
            id=self.session_id,
            organizer_id=self.organizer.id,
            status=GameSessionStatus.ACTIVE,
            name="Evening games",
            points_to_win=11,
            sets_to_win=1,
            is_ranked=True,
            created_at=self.now,
            started_at=self.now,
            completed_at=None,
            version=2,
        )
        self.participants = (
            GameSessionParticipant(
                id=uuid4(),
                session_id=self.session_id,
                user_id=self.organizer.id,
                joined_at=self.now,
                left_at=None,
            ),
            GameSessionParticipant(
                id=uuid4(),
                session_id=self.session_id,
                user_id=self.opponent.id,
                joined_at=self.now,
                left_at=None,
            ),
        )
        self.view = GameSessionView(
            session=self.session,
            active_participants=(
                GameSessionParticipantItem(self.participants[0], self.organizer),
                GameSessionParticipantItem(self.participants[1], self.opponent),
            ),
        )
        self.current = self.make_match(sequence_number=2, status=MatchStatus.ACTIVE)
        self.completed = self.make_match(
            sequence_number=1,
            status=MatchStatus.COMPLETED,
            winner_id=self.organizer.id,
            with_set=True,
        )
        self.queue = GameSessionQueueView(current_pair=self.participants, waiting=())

    def make_match(
        self,
        *,
        sequence_number: int,
        status: MatchStatus,
        winner_id: UUID | None = None,
        with_set: bool = False,
    ) -> MatchView:
        match_id = uuid4()
        match = Match(
            id=match_id,
            context=MatchContext.GAME_SESSION,
            status=status,
            created_by=self.organizer.id,
            points_to_win=11,
            win_by=2,
            sets_to_win=1,
            is_ranked=True,
            winner_id=winner_id,
            created_at=self.now,
            updated_at=self.now,
            started_at=self.now,
            completed_at=self.now if status is MatchStatus.COMPLETED else None,
            cancelled_at=None,
            version=2 if status is MatchStatus.COMPLETED else 1,
            game_session_id=self.session_id,
            sequence_number=sequence_number,
        )
        participants = (
            (
                MatchParticipant(uuid4(), match_id, self.organizer.id, 1),
                self.organizer,
            ),
            (
                MatchParticipant(uuid4(), match_id, self.opponent.id, 2),
                self.opponent,
            ),
        )
        sets = (MatchSet(uuid4(), match_id, 1, 11, 5, self.now),) if with_set else ()
        return MatchView(match=match, participants=participants, sets=sets)


class FakeGameSessionService:
    def __init__(self, fixture: SessionApiFixture) -> None:
        self.fixture = fixture
        self.actions: list[tuple[str, UUID]] = []

    async def create_session(self, uow, *, actor_id: UUID, **kwargs):
        del uow, kwargs
        self.actions.append(("create", actor_id))
        return self.fixture.view

    async def list_sessions(self, repository, *, actor_id: UUID, limit: int, offset: int):
        del repository, actor_id, limit, offset
        return (self.fixture.view,), 1

    async def get_session(self, repository, *, actor_id: UUID, session_id: UUID):
        del repository, actor_id, session_id
        return self.fixture.view

    async def join_session(self, uow, *, actor_id: UUID, session_id: UUID):
        return await self._transition("join", uow, actor_id, session_id)

    async def leave_session(self, uow, *, actor_id: UUID, session_id: UUID):
        return await self._transition("leave", uow, actor_id, session_id)

    async def start_session(self, uow, *, actor_id: UUID, session_id: UUID):
        return await self._transition("start", uow, actor_id, session_id)

    async def complete_session(self, uow, *, actor_id: UUID, session_id: UUID):
        return await self._transition("complete", uow, actor_id, session_id)

    async def cancel_session(self, uow, *, actor_id: UUID, session_id: UUID):
        return await self._transition("cancel", uow, actor_id, session_id)

    async def _transition(self, action: str, uow, actor_id: UUID, session_id: UUID):
        del uow, session_id
        self.actions.append((action, actor_id))
        return self.fixture.view

    async def list_participants(self, repository, **kwargs):
        del repository, kwargs
        return self.fixture.view.active_participants, 2

    async def get_queue(self, repository, **kwargs):
        del repository, kwargs
        return self.fixture.queue

    async def list_session_matches(self, sessions, matches, **kwargs):
        del sessions, matches, kwargs
        return (self.fixture.completed, self.fixture.current), 2

    async def get_current_session_match(self, sessions, matches, **kwargs):
        del sessions, matches, kwargs
        return self.fixture.current

    async def record_session_set(self, uow, **kwargs):
        del uow, kwargs
        return GameSessionMatchResult(
            self.fixture.current, self.fixture.current, self.fixture.queue
        )

    async def record_session_result(self, uow, **kwargs):
        del uow, kwargs
        return GameSessionMatchResult(
            self.fixture.completed, self.fixture.current, self.fixture.queue
        )

    async def cancel_current_match(self, uow, **kwargs):
        del uow, kwargs
        return GameSessionMatchResult(
            self.fixture.completed, self.fixture.current, self.fixture.queue
        )

    async def cancel_current_match_and_leave(self, uow, **kwargs):
        del uow, kwargs
        return GameSessionMatchResult(self.fixture.completed, None, None)


def configured_app(fixture: SessionApiFixture, service) -> object:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_current_user] = lambda: fixture.organizer
    app.dependency_overrides[get_game_session_service] = lambda: service
    app.dependency_overrides[get_game_sessions_uow] = lambda: object()
    app.dependency_overrides[get_game_session_repository] = lambda: object()
    app.dependency_overrides[get_game_session_match_repository] = lambda: object()
    return app


async def test_all_game_session_routes_require_jwt() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = lambda: EmptyUserRepository()
    app.dependency_overrides[get_game_sessions_uow] = lambda: object()
    app.dependency_overrides[get_game_session_repository] = lambda: object()
    app.dependency_overrides[get_game_session_match_repository] = lambda: object()
    session_id = uuid4()
    match_id = uuid4()
    score = {
        "match_id": str(match_id),
        "player1_score": 11,
        "player2_score": 5,
        "expected_version": 1,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.post("/api/v1/game-sessions", json={}),
            await client.get("/api/v1/game-sessions"),
            await client.get(f"/api/v1/game-sessions/{session_id}"),
            await client.post(f"/api/v1/game-sessions/{session_id}/join"),
            await client.post(f"/api/v1/game-sessions/{session_id}/leave"),
            await client.post(f"/api/v1/game-sessions/{session_id}/start"),
            await client.post(f"/api/v1/game-sessions/{session_id}/complete"),
            await client.post(f"/api/v1/game-sessions/{session_id}/cancel"),
            await client.get(f"/api/v1/game-sessions/{session_id}/participants"),
            await client.get(f"/api/v1/game-sessions/{session_id}/queue"),
            await client.get(f"/api/v1/game-sessions/{session_id}/matches"),
            await client.get(f"/api/v1/game-sessions/{session_id}/matches/current"),
            await client.post(
                f"/api/v1/game-sessions/{session_id}/matches/current/sets", json=score
            ),
            await client.put(
                f"/api/v1/game-sessions/{session_id}/matches/current/result",
                json={"match_id": str(match_id), "sets": [score]},
            ),
            await client.post(
                f"/api/v1/game-sessions/{session_id}/matches/current/cancel",
                json={"match_id": str(match_id)},
            ),
            await client.post(
                f"/api/v1/game-sessions/{session_id}/matches/current/cancel-and-leave",
                json={"match_id": str(match_id)},
            ),
        ]

    assert {response.status_code for response in responses} == {401}


async def test_session_lifecycle_participants_queue_and_match_reads() -> None:
    fixture = SessionApiFixture()
    service = FakeGameSessionService(fixture)
    app = configured_app(fixture, service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/game-sessions",
            json={"name": "Evening games", "points_to_win": 11, "sets_to_win": 1},
        )
        listed = await client.get("/api/v1/game-sessions")
        fetched = await client.get(f"/api/v1/game-sessions/{fixture.session_id}")
        transitions = [
            await client.post(f"/api/v1/game-sessions/{fixture.session_id}/{action}")
            for action in ("join", "leave", "start", "complete", "cancel")
        ]
        participants = await client.get(f"/api/v1/game-sessions/{fixture.session_id}/participants")
        queue = await client.get(f"/api/v1/game-sessions/{fixture.session_id}/queue")
        matches = await client.get(f"/api/v1/game-sessions/{fixture.session_id}/matches")
        current = await client.get(f"/api/v1/game-sessions/{fixture.session_id}/matches/current")

    assert created.status_code == 201
    assert listed.json()["total"] == 1
    assert fetched.json()["id"] == str(fixture.session_id)
    assert {response.status_code for response in transitions} == {200}
    assert participants.json()["items"][0]["user"]["id"] == str(fixture.organizer.id)
    assert len(queue.json()["current_pair"]) == 2
    assert queue.json()["waiting"] == []
    assert [item["sequence_number"] for item in matches.json()["items"]] == [1, 2]
    assert current.json()["id"] == str(fixture.current.match.id)
    assert [action for action, _ in service.actions] == [
        "create",
        "join",
        "leave",
        "start",
        "complete",
        "cancel",
    ]


async def test_result_retry_cancel_and_paused_responses() -> None:
    fixture = SessionApiFixture()
    service = FakeGameSessionService(fixture)
    app = configured_app(fixture, service)
    match_id = fixture.current.match.id
    set_payload = {
        "match_id": str(match_id),
        "player1_score": 11,
        "player2_score": 5,
        "expected_version": 1,
    }
    result_payload = {
        "match_id": str(match_id),
        "sets": [{"player1_score": 11, "player2_score": 5}],
        "expected_version": 1,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        added = await client.post(
            f"/api/v1/game-sessions/{fixture.session_id}/matches/current/sets",
            json=set_payload,
        )
        recorded = await client.put(
            f"/api/v1/game-sessions/{fixture.session_id}/matches/current/result",
            json=result_payload,
        )
        replayed = await client.put(
            f"/api/v1/game-sessions/{fixture.session_id}/matches/current/result",
            json=result_payload,
        )
        cancelled = await client.post(
            f"/api/v1/game-sessions/{fixture.session_id}/matches/current/cancel",
            json={"match_id": str(match_id)},
        )
        left = await client.post(
            f"/api/v1/game-sessions/{fixture.session_id}/matches/current/cancel-and-leave",
            json={"match_id": str(match_id)},
        )

    assert added.status_code == 200
    assert recorded.status_code == 200
    assert recorded.json() == replayed.json()
    assert recorded.json()["match"]["winner_id"] == str(fixture.organizer.id)
    assert recorded.json()["current_match"]["id"] == str(fixture.current.match.id)
    assert cancelled.status_code == 200
    assert left.json()["current_match"] is None
    assert left.json()["queue"] is None


async def test_paused_queue_is_explicitly_empty() -> None:
    fixture = SessionApiFixture()
    service = FakeGameSessionService(fixture)

    async def paused_queue(repository, **kwargs):
        del repository, kwargs
        return None

    service.get_queue = paused_queue
    app = configured_app(fixture, service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/game-sessions/{fixture.session_id}/queue")

    assert response.status_code == 200
    assert response.json() == {"current_pair": [], "waiting": [], "is_paused": True}


async def test_expected_application_errors_use_public_http_mapping() -> None:
    fixture = SessionApiFixture()

    class ErrorService(FakeGameSessionService):
        async def get_session(self, repository, **kwargs):
            del repository, kwargs
            raise GameSessionNotFoundError

        async def list_participants(self, repository, **kwargs):
            del repository, kwargs
            raise GameSessionPermissionError

        async def record_session_set(self, uow, **kwargs):
            del uow, kwargs
            raise InvalidScoreError("invalid set")

        async def record_session_result(self, uow, **kwargs):
            del uow, kwargs
            raise MatchStateError("The match is not the current active session match")

    app = configured_app(fixture, ErrorService(fixture))
    base = f"/api/v1/game-sessions/{fixture.session_id}"
    match_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get(base)
        forbidden = await client.get(f"{base}/participants")
        invalid_score = await client.post(
            f"{base}/matches/current/sets",
            json={
                "match_id": str(match_id),
                "player1_score": 11,
                "player2_score": 5,
                "expected_version": 1,
            },
        )
        stale = await client.put(
            f"{base}/matches/current/result",
            json={
                "match_id": str(match_id),
                "sets": [{"player1_score": 11, "player2_score": 5}],
            },
        )
        invalid_transport = await client.post(
            f"{base}/matches/current/sets",
            json={
                "match_id": str(match_id),
                "player1_score": 11.0,
                "player2_score": 5,
                "expected_version": 1,
            },
        )

    assert missing.status_code == 404
    assert forbidden.status_code == 403
    assert invalid_score.status_code == 400
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "match_state_conflict"
    assert invalid_transport.status_code == 422


def test_openapi_contains_all_game_session_routes_and_schemas() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    schema = app.openapi()
    paths = set(schema["paths"])

    assert {
        "/api/v1/game-sessions",
        "/api/v1/game-sessions/{session_id}",
        "/api/v1/game-sessions/{session_id}/join",
        "/api/v1/game-sessions/{session_id}/leave",
        "/api/v1/game-sessions/{session_id}/start",
        "/api/v1/game-sessions/{session_id}/complete",
        "/api/v1/game-sessions/{session_id}/cancel",
        "/api/v1/game-sessions/{session_id}/participants",
        "/api/v1/game-sessions/{session_id}/queue",
        "/api/v1/game-sessions/{session_id}/matches",
        "/api/v1/game-sessions/{session_id}/matches/current",
        "/api/v1/game-sessions/{session_id}/matches/current/sets",
        "/api/v1/game-sessions/{session_id}/matches/current/result",
        "/api/v1/game-sessions/{session_id}/matches/current/cancel",
        "/api/v1/game-sessions/{session_id}/matches/current/cancel-and-leave",
    } <= paths
    assert {
        "SessionCreateRequest",
        "SessionResponse",
        "SessionParticipantResponse",
        "SessionQueueResponse",
        "SessionMatchResponse",
        "SessionSetRequest",
        "SessionResultRequest",
    } <= set(schema["components"]["schemas"])
    operation = schema["paths"]["/api/v1/game-sessions/{session_id}/matches/current/result"]["put"]
    assert {"400", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    assert operation["security"] == [{"HTTPBearer": []}]
    required = schema["components"]["schemas"]["SessionResultRequest"]["required"]
    assert {"match_id", "sets"} <= set(required)


async def test_swagger_ui_is_available_with_game_session_openapi() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/docs")

    assert response.status_code == 200
    assert "/openapi.json" in response.text
    assert "Swagger UI" in response.text
