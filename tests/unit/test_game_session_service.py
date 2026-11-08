"""Game session lifecycle and membership application tests."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from pingstats.modules.game_sessions.application.dto import (
    GameSessionParticipantItem,
    GameSessionView,
)
from pingstats.modules.game_sessions.application.errors import (
    ActiveSessionParticipantExistsError,
    GameSessionCurrentPlayerLeaveError,
    GameSessionPermissionError,
    GameSessionStateError,
    GameSessionUserNotFoundError,
    InvalidGameSessionSettingsError,
)
from pingstats.modules.game_sessions.application.service import GameSessionService
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueEvent,
    GameSessionQueueState,
    GameSessionStatus,
)
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import MatchStateError
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchSet,
    MatchStatus,
    SetScore,
)
from pingstats.modules.users.domain import User


def make_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(user_id, None, name, None, name.lower(), None, now, now, None)


class FakeGameSessionRepository:
    def __init__(self, users: dict[UUID, User]) -> None:
        self.users = users
        self.sessions: dict[UUID, GameSession] = {}
        self.participants: list[GameSessionParticipant] = []
        self.events: list[GameSessionQueueEvent] = []
        self.queue_states: dict[UUID, GameSessionQueueState] = {}
        self.processed_results: set[tuple[UUID, UUID]] = set()
        self.matches = FakeMatchRepository(users)

    async def lock_user(self, user_id: UUID) -> bool:
        return user_id in self.users

    async def user_exists(self, user_id: UUID) -> bool:
        return user_id in self.users

    async def create_session(
        self,
        *,
        organizer_id: UUID,
        name: str | None,
        points_to_win: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> GameSession:
        now = datetime.now(UTC)
        session = GameSession(
            id=uuid4(),
            organizer_id=organizer_id,
            status=GameSessionStatus.PENDING,
            name=name,
            points_to_win=points_to_win,
            sets_to_win=sets_to_win,
            is_ranked=is_ranked,
            created_at=now,
            started_at=None,
            completed_at=None,
            version=1,
        )
        self.sessions[session.id] = session
        return session

    async def get_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> GameSession | None:
        del for_update
        return self.sessions.get(session_id)

    async def get_view(self, session_id: UUID) -> GameSessionView | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        active = tuple(
            GameSessionParticipantItem(participant, self.users[participant.user_id])
            for participant in self.participants
            if participant.session_id == session_id and participant.is_active
        )
        return GameSessionView(session, active)

    async def list_for_user(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[GameSessionView], int]:
        ids = {item.session_id for item in self.participants if item.user_id == user_id}
        views = [await self.get_view(session_id) for session_id in ids]
        resolved = [view for view in views if view is not None]
        return tuple(resolved[offset : offset + limit]), len(resolved)

    async def has_participated(self, session_id: UUID, user_id: UUID) -> bool:
        return any(
            item.session_id == session_id and item.user_id == user_id for item in self.participants
        )

    async def get_active_participant(
        self, session_id: UUID, user_id: UUID
    ) -> GameSessionParticipant | None:
        return next(
            (
                item
                for item in self.participants
                if item.session_id == session_id and item.user_id == user_id and item.is_active
            ),
            None,
        )

    async def add_participant(
        self, session_id: UUID, user_id: UUID, *, joined_at: datetime
    ) -> GameSessionParticipant:
        participant = GameSessionParticipant(uuid4(), session_id, user_id, joined_at, None)
        self.participants.append(participant)
        return participant

    async def close_participant(
        self, participant: GameSessionParticipant, *, left_at: datetime
    ) -> GameSessionParticipant:
        closed = replace(participant, left_at=left_at)
        self.participants[self.participants.index(participant)] = closed
        return closed

    async def count_active_participants(self, session_id: UUID) -> int:
        return sum(item.session_id == session_id and item.is_active for item in self.participants)

    async def list_active_participants(self, session_id: UUID) -> Sequence[GameSessionParticipant]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.participants
                    if item.session_id == session_id and item.is_active
                ),
                key=lambda item: (item.joined_at, item.id),
            )
        )

    async def get_participants_by_ids(
        self, session_id: UUID, participant_ids: Sequence[UUID]
    ) -> Sequence[GameSessionParticipant]:
        by_id = {item.id: item for item in self.participants if item.session_id == session_id}
        return tuple(by_id[item] for item in participant_ids if item in by_id)

    async def get_queue_state(self, session_id: UUID) -> GameSessionQueueState | None:
        return self.queue_states.get(session_id)

    async def save_queue_state(self, session_id: UUID, state: GameSessionQueueState) -> None:
        state.validate()
        self.queue_states[session_id] = state

    async def clear_queue_state(self, session_id: UUID) -> None:
        self.queue_states.pop(session_id, None)

    async def has_processed_result(self, session_id: UUID, result_id: UUID) -> bool:
        return (session_id, result_id) in self.processed_results

    async def mark_result_processed(self, session_id: UUID, result_id: UUID) -> None:
        self.processed_results.add((session_id, result_id))

    async def update_session(
        self,
        session: GameSession,
        *,
        status: GameSessionStatus | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> GameSession:
        updated = replace(
            session,
            status=status or session.status,
            started_at=started_at if started_at is not None else session.started_at,
            completed_at=completed_at if completed_at is not None else session.completed_at,
            version=session.version + 1,
        )
        self.sessions[session.id] = updated
        return updated

    async def add_event(
        self,
        *,
        session_id: UUID,
        participant_id: UUID | None,
        event_type: GameSessionEventType,
        payload: dict[str, object],
    ) -> GameSessionQueueEvent:
        event = GameSessionQueueEvent(
            uuid4(), session_id, participant_id, event_type, payload, datetime.now(UTC)
        )
        self.events.append(event)
        return event

    async def list_participants(
        self,
        session_id: UUID,
        *,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[GameSessionParticipantItem], int]:
        records = [
            item
            for item in self.participants
            if item.session_id == session_id and (not active_only or item.is_active)
        ]
        page = records[offset : offset + limit]
        return (
            tuple(GameSessionParticipantItem(item, self.users[item.user_id]) for item in page),
            len(records),
        )

    async def list_historical_users(self, session_id: UUID) -> Sequence[User]:
        user_ids = {item.user_id for item in self.participants if item.session_id == session_id}
        return tuple(self.users[user_id] for user_id in sorted(user_ids, key=str))


class FakeCurrentMatchChecker:
    def __init__(self) -> None:
        self.active_users: set[tuple[UUID, UUID]] = set()
        self.active_sessions: set[UUID] = set()

    async def has_active_match(self, session_id: UUID, user_id: UUID | None = None) -> bool:
        if user_id is None:
            return session_id in self.active_sessions
        return (session_id, user_id) in self.active_users


class FakeMatchRepository:
    def __init__(self, users: dict[UUID, User]) -> None:
        self.users = users
        self.views: dict[UUID, MatchView] = {}

    async def create_game_session_match(
        self,
        *,
        created_by: UUID,
        session_id: UUID,
        sequence_number: int,
        player1_user_id: UUID,
        player2_user_id: UUID,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        is_ranked: bool,
        started_at: datetime,
    ) -> MatchView:
        match_id = uuid4()
        match = Match(
            id=match_id,
            context=MatchContext.GAME_SESSION,
            status=MatchStatus.ACTIVE,
            created_by=created_by,
            points_to_win=points_to_win,
            win_by=win_by,
            sets_to_win=sets_to_win,
            is_ranked=is_ranked,
            winner_id=None,
            created_at=started_at,
            updated_at=started_at,
            started_at=started_at,
            completed_at=None,
            cancelled_at=None,
            version=1,
            game_session_id=session_id,
            sequence_number=sequence_number,
        )
        participants = tuple(
            (
                MatchParticipant(uuid4(), match_id, user_id, position),
                self.users[user_id],
            )
            for position, user_id in ((1, player1_user_id), (2, player2_user_id))
        )
        view = MatchView(match, participants, ())
        self.views[match_id] = view
        return view

    async def get_view(self, match_id: UUID, *, for_update: bool = False) -> MatchView | None:
        del for_update
        return self.views.get(match_id)

    async def get_current_for_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> MatchView | None:
        del for_update
        return next(
            (
                view
                for view in self.views.values()
                if view.match.game_session_id == session_id
                and view.match.status is MatchStatus.ACTIVE
            ),
            None,
        )

    async def next_session_sequence(self, session_id: UUID) -> int:
        numbers = [
            view.match.sequence_number or 0
            for view in self.views.values()
            if view.match.game_session_id == session_id
        ]
        return max(numbers, default=0) + 1

    async def list_for_session(
        self, session_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchView], int]:
        views = sorted(
            (view for view in self.views.values() if view.match.game_session_id == session_id),
            key=lambda view: view.match.sequence_number or 0,
        )
        return tuple(views[offset : offset + limit]), len(views)

    async def list_completed_for_session(self, session_id: UUID) -> Sequence[MatchView]:
        return tuple(
            sorted(
                (
                    view
                    for view in self.views.values()
                    if view.match.game_session_id == session_id
                    and view.match.status is MatchStatus.COMPLETED
                ),
                key=lambda view: view.match.sequence_number or 0,
            )
        )

    async def update_match(
        self,
        match: Match,
        *,
        status: MatchStatus | None = None,
        winner_id: UUID | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> Match:
        updated = replace(
            match,
            status=status or match.status,
            winner_id=winner_id if winner_id is not None else match.winner_id,
            started_at=started_at if started_at is not None else match.started_at,
            completed_at=completed_at if completed_at is not None else match.completed_at,
            cancelled_at=cancelled_at if cancelled_at is not None else match.cancelled_at,
            updated_at=datetime.now(UTC),
            version=match.version + 1,
        )
        self.views[match.id] = replace(self.views[match.id], match=updated)
        return updated

    async def add_set(self, match_id: UUID, set_number: int, score: SetScore) -> None:
        view = self.views[match_id]
        item = MatchSet(
            uuid4(),
            match_id,
            set_number,
            score.player1_score,
            score.player2_score,
            datetime.now(UTC),
        )
        self.views[match_id] = replace(view, sets=(*view.sets, item))

    async def replace_sets(self, match_id: UUID, scores: Sequence[SetScore]) -> None:
        view = self.views[match_id]
        self.views[match_id] = replace(
            view,
            sets=tuple(
                MatchSet(
                    uuid4(),
                    match_id,
                    number,
                    score.player1_score,
                    score.player2_score,
                    datetime.now(UTC),
                )
                for number, score in enumerate(scores, start=1)
            ),
        )


class FakeUow:
    def __init__(
        self,
        repository: FakeGameSessionRepository,
        checker: FakeCurrentMatchChecker,
    ) -> None:
        self.game_sessions = repository
        self.current_matches = checker
        self.matches = repository.matches

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def setup() -> tuple[
    GameSessionService,
    FakeGameSessionRepository,
    FakeCurrentMatchChecker,
    UUID,
    UUID,
    UUID,
]:
    organizer, player, outsider = uuid4(), uuid4(), uuid4()
    users = {
        organizer: make_user(organizer, "Organizer"),
        player: make_user(player, "Player"),
        outsider: make_user(outsider, "Outsider"),
    }
    return (
        GameSessionService(),
        FakeGameSessionRepository(users),
        FakeCurrentMatchChecker(),
        organizer,
        player,
        outsider,
    )


def uow(repository: FakeGameSessionRepository, checker: FakeCurrentMatchChecker) -> FakeUow:
    return FakeUow(repository, checker)


async def test_create_adds_organizer_and_audits_atomically(setup) -> None:
    service, repository, checker, organizer, _, _ = setup
    created = await service.create_session(
        uow(repository, checker), actor_id=organizer, name="Lunch"
    )

    assert created.session.status is GameSessionStatus.PENDING
    assert (
        created.session.points_to_win,
        created.session.sets_to_win,
        created.session.is_ranked,
    ) == (
        11,
        1,
        True,
    )
    assert [item.user.id for item in created.active_participants] == [organizer]
    assert [event.event_type for event in repository.events] == [
        GameSessionEventType.CREATED,
        GameSessionEventType.JOINED,
    ]

    with pytest.raises(GameSessionUserNotFoundError):
        await service.create_session(uow(repository, checker), actor_id=uuid4())
    with pytest.raises(InvalidGameSessionSettingsError):
        await service.create_session(uow(repository, checker), actor_id=organizer, points_to_win=0)


async def test_join_leave_and_rejoin_preserve_membership_history(setup) -> None:
    service, repository, checker, organizer, player, _ = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    joined = await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    assert {item.user.id for item in joined.active_participants} == {organizer, player}

    with pytest.raises(ActiveSessionParticipantExistsError):
        await service.join_session(
            uow(repository, checker), actor_id=player, session_id=created.session.id
        )
    with pytest.raises(GameSessionPermissionError):
        await service.leave_session(
            uow(repository, checker), actor_id=organizer, session_id=created.session.id
        )

    left = await service.leave_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    assert [item.user.id for item in left.active_participants] == [organizer]
    rejoined = await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    assert {item.user.id for item in rejoined.active_participants} == {organizer, player}
    history, total = await service.list_participants(
        repository,
        actor_id=organizer,
        session_id=created.session.id,
        active_only=False,
        limit=20,
        offset=0,
    )
    assert total == 3
    assert len([item for item in history if item.user.id == player]) == 2


async def test_start_permissions_minimum_and_active_player_leave_guard(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    with pytest.raises(GameSessionStateError):
        await service.start_session(
            uow(repository, checker), actor_id=organizer, session_id=created.session.id
        )
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    with pytest.raises(GameSessionPermissionError):
        await service.start_session(
            uow(repository, checker), actor_id=outsider, session_id=created.session.id
        )

    active = await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    assert active.session.status is GameSessionStatus.ACTIVE
    assert active.session.started_at is not None
    first_match = await repository.matches.get_current_for_session(created.session.id)
    assert first_match is not None
    assert first_match.match.sequence_number == 1
    assert (
        first_match.match.points_to_win,
        first_match.match.sets_to_win,
        first_match.match.is_ranked,
    ) == (11, 1, True)
    with pytest.raises(GameSessionStateError):
        await service.start_session(
            uow(repository, checker), actor_id=organizer, session_id=created.session.id
        )

    with pytest.raises(GameSessionCurrentPlayerLeaveError):
        await service.leave_session(
            uow(repository, checker), actor_id=player, session_id=created.session.id
        )


async def test_complete_cancel_read_permissions_and_forbidden_transitions(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    with pytest.raises(GameSessionPermissionError):
        await service.complete_session(
            uow(repository, checker), actor_id=player, session_id=created.session.id
        )
    completed = await service.complete_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    assert completed.session.status is GameSessionStatus.COMPLETED
    assert completed.session.completed_at is not None
    with pytest.raises(GameSessionStateError):
        await service.join_session(
            uow(repository, checker), actor_id=outsider, session_id=created.session.id
        )

    cancellable = await service.create_session(uow(repository, checker), actor_id=organizer)
    with pytest.raises(GameSessionPermissionError):
        await service.cancel_session(
            uow(repository, checker), actor_id=player, session_id=cancellable.session.id
        )
    cancelled = await service.cancel_session(
        uow(repository, checker), actor_id=organizer, session_id=cancellable.session.id
    )
    assert cancelled.session.status is GameSessionStatus.CANCELLED

    with pytest.raises(GameSessionPermissionError):
        await service.get_session(
            repository,
            actor_id=outsider,
            session_id=created.session.id,
        )
    listed, total = await service.list_sessions(
        repository,
        actor_id=player,
        limit=20,
        offset=0,
    )
    assert total == 1 and listed[0].session.id == created.session.id


async def test_active_join_is_enqueued_and_waiting_leave_reindexes(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )

    pair = await service.get_current_pair(
        repository, actor_id=organizer, session_id=created.session.id
    )
    assert tuple(item.user_id for item in pair) == (organizer, player)

    await service.join_session(
        uow(repository, checker), actor_id=outsider, session_id=created.session.id
    )
    waiting = await service.get_waiting_queue(
        repository, actor_id=player, session_id=created.session.id
    )
    assert [item.user_id for item in waiting] == [outsider]
    assert repository.events[-1].event_type is GameSessionEventType.PLAYER_ENQUEUED

    await service.leave_session(
        uow(repository, checker), actor_id=outsider, session_id=created.session.id
    )
    assert (
        await service.get_waiting_queue(
            repository, actor_id=organizer, session_id=created.session.id
        )
        == ()
    )
    assert repository.events[-1].event_type is GameSessionEventType.PLAYER_LEFT_QUEUE


async def test_rotation_is_idempotent_and_cancel_preserves_queue(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.join_session(
        uow(repository, checker), actor_id=outsider, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    first_match = await repository.matches.get_current_for_session(created.session.id)
    assert first_match is not None
    rotated = await service.record_session_result(
        uow(repository, checker),
        actor_id=player,
        session_id=created.session.id,
        match_id=first_match.match.id,
        scores=[SetScore(11, 4)],
    )
    assert rotated.queue is not None
    assert tuple(item.user_id for item in rotated.queue.current_pair) == (organizer, outsider)
    assert [item.user_id for item in rotated.queue.waiting] == [player]
    event_count = len(repository.events)

    repeated = await service.record_session_result(
        uow(repository, checker),
        actor_id=player,
        session_id=created.session.id,
        match_id=first_match.match.id,
        scores=[SetScore(11, 4)],
    )
    assert repeated == rotated
    assert len(repository.events) == event_count

    assert rotated.current_match is not None
    restored = await service.cancel_current_match(
        uow(repository, checker),
        actor_id=outsider,
        session_id=created.session.id,
        match_id=rotated.current_match.match.id,
    )
    assert restored.queue == rotated.queue
    assert restored.current_match is not None
    assert restored.current_match.match.sequence_number == 3


@pytest.mark.parametrize(
    ("target", "expected_pair", "expected_waiting"),
    [
        ("first", ("player", "outsider"), ("organizer",)),
        ("second", ("organizer", "outsider"), ("player",)),
        ("both", ("outsider", "organizer"), ("player",)),
    ],
)
async def test_skip_current_match_rotates_without_result_or_rating_input(
    setup, target: str, expected_pair: tuple[str, str], expected_waiting: tuple[str, ...]
) -> None:
    service, repository, checker, organizer, player, outsider = setup
    users = {"organizer": organizer, "player": player, "outsider": outsider}
    created = await service.create_session(
        uow(repository, checker), actor_id=organizer, is_ranked=True
    )
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.join_session(
        uow(repository, checker), actor_id=outsider, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    current = await repository.matches.get_current_for_session(created.session.id)
    assert current is not None

    result = await service.skip_current_match(
        uow(repository, checker),
        actor_id=organizer,
        session_id=created.session.id,
        match_id=current.match.id,
        skipped_player_id=(
            None if target == "both" else organizer if target == "first" else player
        ),
        skip_both=target == "both",
    )

    assert result.match.match.status is MatchStatus.CANCELLED
    assert result.match.match.winner_id is None
    assert result.match.sets == ()
    assert (created.session.id, current.match.id) not in repository.processed_results
    assert result.current_match is not None
    assert tuple(item.user_id for item in result.queue.current_pair) == tuple(
        users[name] for name in expected_pair
    )
    assert tuple(item.user_id for item in result.queue.waiting) == tuple(
        users[name] for name in expected_waiting
    )
    assert repository.events[-2].event_type is GameSessionEventType.CURRENT_GAME_SKIPPED

    with pytest.raises(MatchStateError):
        await service.skip_current_match(
            uow(repository, checker),
            actor_id=organizer,
            session_id=created.session.id,
            match_id=current.match.id,
            skipped_player_id=organizer,
            skip_both=False,
        )
    await service.complete_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    with pytest.raises(GameSessionStateError):
        await service.skip_current_match(
            uow(repository, checker),
            actor_id=organizer,
            session_id=created.session.id,
            match_id=result.current_match.match.id,
            skipped_player_id=None,
            skip_both=True,
        )


async def test_incremental_result_and_cancel_leave_pause_then_join_resume(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(
        uow(repository, checker), actor_id=organizer, sets_to_win=2
    )
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )
    first_match = await repository.matches.get_current_for_session(created.session.id)
    assert first_match is not None

    partial = await service.record_session_set(
        uow(repository, checker),
        actor_id=organizer,
        session_id=created.session.id,
        match_id=first_match.match.id,
        score=SetScore(11, 3),
        expected_version=first_match.match.version,
    )
    assert partial.match.match.status is MatchStatus.ACTIVE
    assert partial.current_match == partial.match
    completed = await service.record_session_set(
        uow(repository, checker),
        actor_id=player,
        session_id=created.session.id,
        match_id=first_match.match.id,
        score=SetScore(12, 10),
        expected_version=partial.match.match.version,
    )
    assert completed.match.match.status is MatchStatus.COMPLETED
    assert completed.current_match is not None
    assert completed.current_match.match.sequence_number == 2

    paused = await service.cancel_current_match_and_leave(
        uow(repository, checker),
        actor_id=player,
        session_id=created.session.id,
        match_id=completed.current_match.match.id,
    )
    assert paused.current_match is None and paused.queue is None
    assert await repository.matches.get_current_for_session(created.session.id) is None

    await service.join_session(
        uow(repository, checker), actor_id=outsider, session_id=created.session.id
    )
    resumed = await repository.matches.get_current_for_session(created.session.id)
    assert resumed is not None
    assert resumed.match.sequence_number == 3
    assert resumed.participant_ids() == {organizer, outsider}


async def test_session_match_and_paused_queue_reads_enforce_access(setup) -> None:
    service, repository, checker, organizer, player, outsider = setup
    created = await service.create_session(uow(repository, checker), actor_id=organizer)
    await service.join_session(
        uow(repository, checker), actor_id=player, session_id=created.session.id
    )
    await service.start_session(
        uow(repository, checker), actor_id=organizer, session_id=created.session.id
    )

    current = await service.get_current_session_match(
        repository,
        repository.matches,
        actor_id=player,
        session_id=created.session.id,
    )
    history, total = await service.list_session_matches(
        repository,
        repository.matches,
        actor_id=player,
        session_id=created.session.id,
        limit=20,
        offset=0,
    )
    queue = await service.get_queue(repository, actor_id=player, session_id=created.session.id)
    assert history == (current,) and total == 1
    assert queue is not None and len(queue.current_pair) == 2

    with pytest.raises(GameSessionPermissionError):
        await service.list_session_matches(
            repository,
            repository.matches,
            actor_id=outsider,
            session_id=created.session.id,
            limit=20,
            offset=0,
        )

    paused = await service.cancel_current_match_and_leave(
        uow(repository, checker),
        actor_id=player,
        session_id=created.session.id,
        match_id=current.match.id,
    )
    assert paused.current_match is None
    assert (
        await service.get_queue(repository, actor_id=organizer, session_id=created.session.id)
        is None
    )
