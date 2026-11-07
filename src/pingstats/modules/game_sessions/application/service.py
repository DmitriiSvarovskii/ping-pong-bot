"""Game session lifecycle and membership use cases."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from pingstats.modules.game_sessions.application.dto import (
    GameSessionMatchResult,
    GameSessionParticipantItem,
    GameSessionQueueView,
    GameSessionView,
)
from pingstats.modules.game_sessions.application.errors import (
    ActiveSessionParticipantExistsError,
    ActiveSessionParticipantNotFoundError,
    GameSessionCurrentPlayerLeaveError,
    GameSessionHasActiveMatchError,
    GameSessionNotFoundError,
    GameSessionPermissionError,
    GameSessionQueueConflictError,
    GameSessionQueueNotInitializedError,
    GameSessionStateError,
    GameSessionUserNotFoundError,
    InvalidGameSessionSettingsError,
)
from pingstats.modules.game_sessions.application.ports import (
    GameSessionRepository,
    GameSessionsUnitOfWork,
)
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueState,
    GameSessionStatus,
    InvalidQueueStateError,
    LoserRotatesStrategy,
    append_waiting,
    initialize_queue,
    remove_waiting,
)
from pingstats.modules.game_sessions.domain import (
    restore_queue_after_cancel as restore_cancelled_queue,
)
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import (
    MatchNotFoundError,
    MatchPermissionError,
    MatchStateError,
    MatchVersionConflictError,
)
from pingstats.modules.matches.application.ports import MatchRepository
from pingstats.modules.matches.application.result_writer import MatchResultWriter
from pingstats.modules.matches.domain import MatchStatus, SetScore


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GameSessionService:
    """Coordinate session rules inside one explicit transaction per command."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        rotation_strategy: LoserRotatesStrategy | None = None,
        result_writer: MatchResultWriter | None = None,
    ) -> None:
        self._clock = clock
        self._rotation_strategy = rotation_strategy or LoserRotatesStrategy()
        self._result_writer = result_writer or MatchResultWriter(clock=clock)

    async def create_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        name: str | None = None,
        points_to_win: int = 11,
        sets_to_win: int = 1,
        is_ranked: bool = True,
    ) -> GameSessionView:
        self._validate_settings(name, points_to_win, sets_to_win, is_ranked)
        async with uow:
            if not await uow.game_sessions.lock_user(actor_id):
                raise GameSessionUserNotFoundError
            session = await uow.game_sessions.create_session(
                organizer_id=actor_id,
                name=name,
                points_to_win=points_to_win,
                sets_to_win=sets_to_win,
                is_ranked=is_ranked,
            )
            participant = await uow.game_sessions.add_participant(
                session.id,
                actor_id,
                joined_at=self._clock(),
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=None,
                event_type=GameSessionEventType.CREATED,
                payload={"actor_id": str(actor_id)},
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=participant.id,
                event_type=GameSessionEventType.JOINED,
                payload={"actor_id": str(actor_id), "role": "organizer"},
            )
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def get_session(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        await self._require_read_access(repository, actor_id, session_id)
        return await self._required_view(repository, session_id)

    async def list_sessions(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[GameSessionView], int]:
        if not await repository.user_exists(actor_id):
            raise GameSessionUserNotFoundError
        return await repository.list_for_user(actor_id, limit=limit, offset=offset)

    async def join_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        async with uow:
            session = await self._locked_session(uow.game_sessions, session_id)
            if session.status not in (GameSessionStatus.PENDING, GameSessionStatus.ACTIVE):
                raise GameSessionStateError("Completed or cancelled sessions cannot be joined")
            if not await uow.game_sessions.user_exists(actor_id):
                raise GameSessionUserNotFoundError
            if await uow.game_sessions.get_active_participant(session.id, actor_id) is not None:
                raise ActiveSessionParticipantExistsError
            previous_queue = (
                await uow.game_sessions.get_queue_state(session.id)
                if session.status is GameSessionStatus.ACTIVE
                else None
            )
            participant = await uow.game_sessions.add_participant(
                session.id,
                actor_id,
                joined_at=self._clock(),
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=participant.id,
                event_type=GameSessionEventType.JOINED,
                payload={"actor_id": str(actor_id)},
            )
            if previous_queue is not None:
                try:
                    updated_queue = append_waiting(previous_queue, participant.id)
                except InvalidQueueStateError as error:
                    raise GameSessionQueueConflictError(str(error)) from error
                await uow.game_sessions.save_queue_state(session.id, updated_queue)
                await uow.game_sessions.add_event(
                    session_id=session.id,
                    participant_id=participant.id,
                    event_type=GameSessionEventType.PLAYER_ENQUEUED,
                    payload=self._queue_payload(
                        previous_queue,
                        updated_queue,
                        reason="active_participant_joined",
                        participant_id=participant.id,
                    ),
                )
            elif session.status is GameSessionStatus.ACTIVE:
                participants = await uow.game_sessions.list_active_participants(session.id)
                if len(participants) < 2:
                    raise GameSessionQueueConflictError(
                        "An active paused session requires two participants to resume"
                    )
                try:
                    updated_queue = initialize_queue(participants)
                except InvalidQueueStateError as error:
                    raise GameSessionQueueConflictError(str(error)) from error
                await uow.game_sessions.save_queue_state(session.id, updated_queue)
                await uow.game_sessions.add_event(
                    session_id=session.id,
                    participant_id=participant.id,
                    event_type=GameSessionEventType.QUEUE_INITIALIZED,
                    payload=self._queue_payload(
                        None,
                        updated_queue,
                        reason="paused_session_resumed_by_join",
                        participant_id=participant.id,
                    ),
                )
                await self._create_next_match(uow, session, updated_queue)
            await uow.game_sessions.update_session(session)
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def leave_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        async with uow:
            session = await self._locked_session(uow.game_sessions, session_id)
            if session.status not in (GameSessionStatus.PENDING, GameSessionStatus.ACTIVE):
                raise GameSessionStateError("Completed or cancelled sessions cannot be left")
            if session.organizer_id == actor_id:
                raise GameSessionPermissionError(
                    "The organizer must complete or cancel the session"
                )
            participant = await uow.game_sessions.get_active_participant(session.id, actor_id)
            if participant is None:
                raise ActiveSessionParticipantNotFoundError
            updated_queue: GameSessionQueueState | None = None
            previous_queue: GameSessionQueueState | None = None
            if session.status is GameSessionStatus.ACTIVE:
                previous_queue = await self._required_queue_state(uow.game_sessions, session.id)
                if participant.id in previous_queue.current_pair:
                    raise GameSessionCurrentPlayerLeaveError
                if await uow.current_matches.has_active_match(session.id, actor_id):
                    raise GameSessionHasActiveMatchError
                try:
                    updated_queue = remove_waiting(previous_queue, participant.id)
                except InvalidQueueStateError as error:
                    raise GameSessionQueueConflictError(str(error)) from error
                await uow.game_sessions.save_queue_state(session.id, updated_queue)
            await uow.game_sessions.close_participant(participant, left_at=self._clock())
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=participant.id,
                event_type=GameSessionEventType.LEFT,
                payload={"actor_id": str(actor_id)},
            )
            if previous_queue is not None and updated_queue is not None:
                await uow.game_sessions.add_event(
                    session_id=session.id,
                    participant_id=participant.id,
                    event_type=GameSessionEventType.PLAYER_LEFT_QUEUE,
                    payload=self._queue_payload(
                        previous_queue,
                        updated_queue,
                        reason="waiting_participant_left",
                        participant_id=participant.id,
                    ),
                )
            await uow.game_sessions.update_session(session)
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def start_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        async with uow:
            session = await self._locked_session(uow.game_sessions, session_id)
            self._require_organizer(session, actor_id)
            if session.status is not GameSessionStatus.PENDING:
                raise GameSessionStateError("Only a pending session can be started")
            participants = await uow.game_sessions.list_active_participants(session.id)
            if len(participants) < 2:
                raise GameSessionStateError("At least two active participants are required")
            try:
                queue_state = initialize_queue(participants)
            except InvalidQueueStateError as error:
                raise GameSessionQueueConflictError(str(error)) from error
            await uow.game_sessions.save_queue_state(session.id, queue_state)
            session = await uow.game_sessions.update_session(
                session,
                status=GameSessionStatus.ACTIVE,
                started_at=self._clock(),
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=None,
                event_type=GameSessionEventType.STARTED,
                payload={"actor_id": str(actor_id)},
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=None,
                event_type=GameSessionEventType.QUEUE_INITIALIZED,
                payload=self._queue_payload(
                    None,
                    queue_state,
                    reason="session_started",
                ),
            )
            await self._create_next_match(uow, session, queue_state)
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def complete_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        async with uow:
            session = await self._locked_session(uow.game_sessions, session_id)
            self._require_organizer(session, actor_id)
            if session.status is not GameSessionStatus.ACTIVE:
                raise GameSessionStateError("Only an active session can be completed")
            current_match = await uow.matches.get_current_for_session(session.id, for_update=True)
            if current_match is not None:
                cancelled = await uow.matches.update_match(
                    current_match.match,
                    status=MatchStatus.CANCELLED,
                    cancelled_at=self._clock(),
                )
                await uow.game_sessions.add_event(
                    session_id=session.id,
                    participant_id=None,
                    event_type=GameSessionEventType.MATCH_CANCELLED,
                    payload={
                        "actor_id": str(actor_id),
                        "match_id": str(cancelled.id),
                        "reason": "session_completed",
                    },
                )
            session = await uow.game_sessions.update_session(
                session,
                status=GameSessionStatus.COMPLETED,
                completed_at=self._clock(),
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=None,
                event_type=GameSessionEventType.COMPLETED,
                payload={"actor_id": str(actor_id)},
            )
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def cancel_session(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionView:
        async with uow:
            session = await self._locked_session(uow.game_sessions, session_id)
            self._require_organizer(session, actor_id)
            if session.status is not GameSessionStatus.PENDING:
                raise GameSessionStateError("Only a pending session can be cancelled")
            session = await uow.game_sessions.update_session(
                session,
                status=GameSessionStatus.CANCELLED,
            )
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=None,
                event_type=GameSessionEventType.CANCELLED,
                payload={"actor_id": str(actor_id)},
            )
            view = await self._required_view(uow.game_sessions, session.id)
            await uow.commit()
        return view

    async def list_participants(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
        active_only: bool = True,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[GameSessionParticipantItem], int]:
        await self._require_read_access(repository, actor_id, session_id)
        return await repository.list_participants(
            session_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    async def get_current_pair(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> tuple[GameSessionParticipant, GameSessionParticipant]:
        await self._require_queue_access(repository, actor_id, session_id)
        return (await self._required_queue_view(repository, session_id)).current_pair

    async def get_waiting_queue(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> tuple[GameSessionParticipant, ...]:
        await self._require_queue_access(repository, actor_id, session_id)
        return (await self._required_queue_view(repository, session_id)).waiting

    async def get_queue(
        self,
        repository: GameSessionRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> GameSessionQueueView | None:
        await self._require_queue_access(repository, actor_id, session_id)
        if await repository.get_queue_state(session_id) is None:
            return None
        return await self._required_queue_view(repository, session_id)

    async def list_session_matches(
        self,
        sessions: GameSessionRepository,
        matches: MatchRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MatchView], int]:
        await self._require_read_access(sessions, actor_id, session_id)
        return await matches.list_for_session(session_id, limit=limit, offset=offset)

    async def get_current_session_match(
        self,
        sessions: GameSessionRepository,
        matches: MatchRepository,
        *,
        actor_id: UUID,
        session_id: UUID,
    ) -> MatchView:
        await self._require_read_access(sessions, actor_id, session_id)
        current = await matches.get_current_for_session(session_id)
        if current is None:
            raise MatchNotFoundError
        return current

    async def record_session_set(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
        match_id: UUID,
        score: SetScore,
        expected_version: int,
    ) -> GameSessionMatchResult:
        async with uow:
            session = await self._locked_active_session(uow.game_sessions, session_id)
            await self._require_active_participant(uow.game_sessions, session.id, actor_id)
            replay = await self._processed_result(
                uow.game_sessions, uow.matches, session.id, match_id
            )
            if replay is not None:
                recorded, current = replay
                if not recorded.sets or recorded.sets[-1].score() != score:
                    raise MatchStateError("A completed session result cannot be changed")
                return GameSessionMatchResult(
                    recorded,
                    current,
                    await self._required_queue_view(uow.game_sessions, session.id),
                )
            current = await self._locked_current_match(uow.matches, session.id, match_id)
            if current.match.version != expected_version:
                raise MatchVersionConflictError
            updated = await self._result_writer.add_set(uow.matches, current, score)
            if updated.match.status is MatchStatus.COMPLETED:
                result = await self._advance_after_result(uow, session, updated)
            else:
                result = GameSessionMatchResult(
                    updated,
                    updated,
                    await self._required_queue_view(uow.game_sessions, session.id),
                )
            await uow.commit()
        return result

    async def record_session_result(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
        match_id: UUID,
        scores: Sequence[SetScore],
        expected_version: int | None = None,
    ) -> GameSessionMatchResult:
        async with uow:
            session = await self._locked_active_session(uow.game_sessions, session_id)
            await self._require_active_participant(uow.game_sessions, session.id, actor_id)
            replay = await self._processed_result(
                uow.game_sessions, uow.matches, session.id, match_id
            )
            if replay is not None:
                recorded, current = replay
                if tuple(item.score() for item in recorded.sets) != tuple(scores):
                    raise MatchStateError("A completed session result cannot be changed")
                return GameSessionMatchResult(
                    recorded,
                    current,
                    await self._required_queue_view(uow.game_sessions, session.id),
                )
            current = await self._locked_current_match(uow.matches, session.id, match_id)
            if expected_version is not None and current.match.version != expected_version:
                raise MatchVersionConflictError
            updated = await self._result_writer.record_full_result(uow.matches, current, scores)
            result = await self._advance_after_result(uow, session, updated)
            await uow.commit()
        return result

    async def cancel_current_match(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
        match_id: UUID,
    ) -> GameSessionMatchResult:
        async with uow:
            session = await self._locked_active_session(uow.game_sessions, session_id)
            current = await self._locked_current_match(uow.matches, session.id, match_id)
            if session.organizer_id != actor_id and actor_id not in current.participant_ids():
                raise MatchPermissionError(
                    "Only the organizer or a current player can cancel this match"
                )
            cancelled = await self._cancel_match(uow, session, current, actor_id)
            previous = await self._required_queue_state(uow.game_sessions, session.id)
            participants = await uow.game_sessions.list_active_participants(session.id)
            try:
                restored = restore_cancelled_queue(
                    previous,
                    (participant.id for participant in participants),
                )
            except InvalidQueueStateError as error:
                raise GameSessionQueueConflictError(str(error)) from error
            await uow.game_sessions.save_queue_state(session.id, restored)
            await self._record_cancelled_game_event(
                uow.game_sessions,
                session.id,
                previous,
                restored,
                current.match.id,
                actor_id,
            )
            successor = await self._create_next_match(uow, session, restored)
            await uow.game_sessions.update_session(session)
            result = GameSessionMatchResult(
                await self._required_match_view(uow.matches, cancelled.id),
                successor,
                await self._required_queue_view(uow.game_sessions, session.id),
            )
            await uow.commit()
        return result

    async def cancel_current_match_and_leave(
        self,
        uow: GameSessionsUnitOfWork,
        *,
        actor_id: UUID,
        session_id: UUID,
        match_id: UUID,
    ) -> GameSessionMatchResult:
        async with uow:
            session = await self._locked_active_session(uow.game_sessions, session_id)
            if session.organizer_id == actor_id:
                raise GameSessionPermissionError(
                    "The organizer must complete the session and cannot leave it"
                )
            participant = await self._require_active_participant(
                uow.game_sessions, session.id, actor_id
            )
            current = await self._locked_current_match(uow.matches, session.id, match_id)
            previous = await self._required_queue_state(uow.game_sessions, session.id)
            if participant.id not in previous.current_pair:
                raise GameSessionCurrentPlayerLeaveError
            cancelled = await self._cancel_match(uow, session, current, actor_id)
            other_id = next(item for item in previous.current_pair if item != participant.id)
            updated: GameSessionQueueState | None
            if previous.waiting:
                updated = GameSessionQueueState(
                    current_pair=(other_id, previous.waiting[0]),
                    waiting=previous.waiting[1:],
                )
                updated.validate()
                await uow.game_sessions.save_queue_state(session.id, updated)
            else:
                updated = None
                await uow.game_sessions.clear_queue_state(session.id)
            await uow.game_sessions.close_participant(participant, left_at=self._clock())
            await uow.game_sessions.add_event(
                session_id=session.id,
                participant_id=participant.id,
                event_type=GameSessionEventType.LEFT,
                payload={"actor_id": str(actor_id), "reason": "current_match_cancelled"},
            )
            await self._record_cancelled_game_event(
                uow.game_sessions,
                session.id,
                previous,
                updated,
                current.match.id,
                actor_id,
            )
            successor = (
                await self._create_next_match(uow, session, updated)
                if updated is not None
                else None
            )
            await uow.game_sessions.update_session(session)
            queue_view = (
                await self._required_queue_view(uow.game_sessions, session.id)
                if updated is not None
                else None
            )
            result = GameSessionMatchResult(
                await self._required_match_view(uow.matches, cancelled.id),
                successor,
                queue_view,
            )
            await uow.commit()
        return result

    async def _advance_after_result(
        self,
        uow: GameSessionsUnitOfWork,
        session: GameSession,
        completed: MatchView,
    ) -> GameSessionMatchResult:
        winner_user_id = completed.match.winner_id
        if winner_user_id is None:
            raise MatchStateError("A completed match must have a winner")
        winner = await uow.game_sessions.get_active_participant(session.id, winner_user_id)
        if winner is None:
            raise GameSessionQueueConflictError("Match winner is not an active participant")
        previous = await self._required_queue_state(uow.game_sessions, session.id)
        try:
            updated = self._rotation_strategy.rotate(previous, winner.id)
        except InvalidQueueStateError as error:
            raise GameSessionQueueConflictError(str(error)) from error
        await uow.game_sessions.save_queue_state(session.id, updated)
        await uow.game_sessions.mark_result_processed(session.id, completed.match.id)
        await self._record_rotation_events(
            uow.game_sessions,
            session.id,
            previous,
            updated,
            winner.id,
            completed.match.id,
        )
        successor = await self._create_next_match(uow, session, updated)
        await uow.game_sessions.update_session(session)
        return GameSessionMatchResult(
            completed,
            successor,
            await self._required_queue_view(uow.game_sessions, session.id),
        )

    async def _create_next_match(
        self,
        uow: GameSessionsUnitOfWork,
        session: GameSession,
        state: GameSessionQueueState,
    ) -> MatchView:
        participants = await uow.game_sessions.get_participants_by_ids(
            session.id, state.current_pair
        )
        if len(participants) != 2:
            raise GameSessionQueueConflictError("Current pair references missing participants")
        by_id = {participant.id: participant for participant in participants}
        sequence_number = await uow.matches.next_session_sequence(session.id)
        view = await uow.matches.create_game_session_match(
            created_by=session.organizer_id,
            session_id=session.id,
            sequence_number=sequence_number,
            player1_user_id=by_id[state.current_pair[0]].user_id,
            player2_user_id=by_id[state.current_pair[1]].user_id,
            points_to_win=session.points_to_win,
            win_by=2,
            sets_to_win=session.sets_to_win,
            is_ranked=session.is_ranked,
            started_at=self._clock(),
        )
        await uow.game_sessions.add_event(
            session_id=session.id,
            participant_id=None,
            event_type=GameSessionEventType.MATCH_STARTED,
            payload={
                "match_id": str(view.match.id),
                "sequence_number": sequence_number,
                "participant_ids": [str(item) for item in state.current_pair],
            },
        )
        return view

    async def _cancel_match(
        self,
        uow: GameSessionsUnitOfWork,
        session: GameSession,
        current: MatchView,
        actor_id: UUID,
    ):
        cancelled = await uow.matches.update_match(
            current.match,
            status=MatchStatus.CANCELLED,
            cancelled_at=self._clock(),
        )
        await uow.game_sessions.add_event(
            session_id=session.id,
            participant_id=None,
            event_type=GameSessionEventType.MATCH_CANCELLED,
            payload={
                "actor_id": str(actor_id),
                "match_id": str(cancelled.id),
                "sequence_number": cancelled.sequence_number,
                "reason": "current_match_cancelled",
            },
        )
        return cancelled

    async def _record_cancelled_game_event(
        self,
        repository: GameSessionRepository,
        session_id: UUID,
        previous: GameSessionQueueState,
        updated: GameSessionQueueState | None,
        match_id: UUID,
        actor_id: UUID,
    ) -> None:
        await repository.add_event(
            session_id=session_id,
            participant_id=None,
            event_type=GameSessionEventType.CURRENT_GAME_CANCELLED,
            payload={
                **self._queue_payload(
                    previous,
                    updated,
                    reason="current_match_cancelled_without_result",
                    result_id=match_id,
                ),
                "actor_id": str(actor_id),
                "match_id": str(match_id),
            },
        )

    async def _record_rotation_events(
        self,
        repository: GameSessionRepository,
        session_id: UUID,
        previous: GameSessionQueueState,
        updated: GameSessionQueueState,
        winner_id: UUID,
        match_id: UUID,
    ) -> None:
        if previous.waiting:
            challenger_id = previous.waiting[0]
            loser_id = next(item for item in previous.current_pair if item != winner_id)
            await repository.add_event(
                session_id=session_id,
                participant_id=challenger_id,
                event_type=GameSessionEventType.PLAYER_DEQUEUED,
                payload=self._queue_payload(
                    previous,
                    updated,
                    reason="selected_as_next_challenger",
                    participant_id=challenger_id,
                    result_id=match_id,
                ),
            )
            await repository.add_event(
                session_id=session_id,
                participant_id=loser_id,
                event_type=GameSessionEventType.PLAYER_ENQUEUED,
                payload=self._queue_payload(
                    previous,
                    updated,
                    reason="loser_rotated_to_tail",
                    participant_id=loser_id,
                    result_id=match_id,
                ),
            )
        await repository.add_event(
            session_id=session_id,
            participant_id=winner_id,
            event_type=GameSessionEventType.ROTATION,
            payload=self._queue_payload(
                previous,
                updated,
                reason="completed_match",
                participant_id=winner_id,
                result_id=match_id,
            ),
        )

    @staticmethod
    async def _processed_result(
        sessions: GameSessionRepository,
        matches: MatchRepository,
        session_id: UUID,
        match_id: UUID,
    ) -> tuple[MatchView, MatchView] | None:
        if not await sessions.has_processed_result(session_id, match_id):
            return None
        recorded = await matches.get_view(match_id)
        current = await matches.get_current_for_session(session_id)
        if (
            recorded is None
            or recorded.match.game_session_id != session_id
            or recorded.match.status is not MatchStatus.COMPLETED
            or current is None
        ):
            raise GameSessionStateError("Processed session result is inconsistent")
        return recorded, current

    @staticmethod
    async def _locked_current_match(
        repository: MatchRepository, session_id: UUID, match_id: UUID
    ) -> MatchView:
        current = await repository.get_current_for_session(session_id, for_update=True)
        if current is None or current.match.id != match_id:
            raise MatchStateError("The match is not the current active session match")
        return current

    @classmethod
    async def _locked_active_session(
        cls, repository: GameSessionRepository, session_id: UUID
    ) -> GameSession:
        session = await cls._locked_session(repository, session_id)
        if session.status is not GameSessionStatus.ACTIVE:
            raise GameSessionStateError("This operation requires an active session")
        return session

    @staticmethod
    async def _require_active_participant(
        repository: GameSessionRepository, session_id: UUID, actor_id: UUID
    ) -> GameSessionParticipant:
        participant = await repository.get_active_participant(session_id, actor_id)
        if participant is None:
            raise GameSessionPermissionError(
                "Only an active session participant may record a result"
            )
        return participant

    @staticmethod
    async def _required_match_view(repository: MatchRepository, match_id: UUID) -> MatchView:
        view = await repository.get_view(match_id)
        if view is None:
            raise MatchStateError("Session match could not be loaded")
        return view

    @staticmethod
    async def _locked_session(repository: GameSessionRepository, session_id: UUID) -> GameSession:
        session = await repository.get_session(session_id, for_update=True)
        if session is None:
            raise GameSessionNotFoundError
        return session

    @staticmethod
    async def _required_view(
        repository: GameSessionRepository, session_id: UUID
    ) -> GameSessionView:
        view = await repository.get_view(session_id)
        if view is None:
            raise GameSessionNotFoundError
        return view

    @staticmethod
    async def _required_queue_state(
        repository: GameSessionRepository, session_id: UUID
    ) -> GameSessionQueueState:
        state = await repository.get_queue_state(session_id)
        if state is None:
            raise GameSessionQueueNotInitializedError
        return state

    @classmethod
    async def _required_queue_view(
        cls, repository: GameSessionRepository, session_id: UUID
    ) -> GameSessionQueueView:
        state = await cls._required_queue_state(repository, session_id)
        ids = (*state.current_pair, *state.waiting)
        participants = await repository.get_participants_by_ids(session_id, ids)
        by_id = {participant.id: participant for participant in participants}
        if len(by_id) != len(ids):
            raise GameSessionQueueConflictError("Queue references missing participants")
        return GameSessionQueueView(
            current_pair=(by_id[state.current_pair[0]], by_id[state.current_pair[1]]),
            waiting=tuple(by_id[participant_id] for participant_id in state.waiting),
        )

    @staticmethod
    async def _require_read_access(
        repository: GameSessionRepository, actor_id: UUID, session_id: UUID
    ) -> None:
        if await repository.get_session(session_id) is None:
            raise GameSessionNotFoundError
        if not await repository.has_participated(session_id, actor_id):
            raise GameSessionPermissionError("Only session participants can view this resource")

    @staticmethod
    async def _require_queue_access(
        repository: GameSessionRepository, actor_id: UUID, session_id: UUID
    ) -> None:
        session = await repository.get_session(session_id)
        if session is None:
            raise GameSessionNotFoundError
        if session.status is not GameSessionStatus.ACTIVE:
            raise GameSessionStateError("Queue state is available only for an active session")
        if await repository.get_active_participant(session_id, actor_id) is None:
            raise GameSessionPermissionError("Only active participants can view the queue")

    @staticmethod
    def _queue_payload(
        previous: GameSessionQueueState | None,
        updated: GameSessionQueueState | None,
        *,
        reason: str,
        participant_id: UUID | None = None,
        result_id: UUID | None = None,
    ) -> dict[str, object]:
        def snapshot(state: GameSessionQueueState | None) -> dict[str, list[str]] | None:
            if state is None:
                return None
            return {
                "current_pair": [str(item) for item in state.current_pair],
                "waiting": [str(item) for item in state.waiting],
            }

        payload: dict[str, object] = {
            "reason": reason,
            "previous": snapshot(previous),
            "current": snapshot(updated),
        }
        if participant_id is not None:
            payload["participant_id"] = str(participant_id)
        if result_id is not None:
            payload["result_id"] = str(result_id)
        return payload

    @staticmethod
    def _require_organizer(session: GameSession, actor_id: UUID) -> None:
        if session.organizer_id != actor_id:
            raise GameSessionPermissionError("Only the organizer can perform this operation")

    @staticmethod
    def _validate_settings(
        name: str | None,
        points_to_win: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> None:
        if name is not None and len(name) > 128:
            raise InvalidGameSessionSettingsError("Session name must not exceed 128 characters")
        if type(points_to_win) is not int or points_to_win <= 0:
            raise InvalidGameSessionSettingsError("points_to_win must be a positive integer")
        if type(sets_to_win) is not int or sets_to_win <= 0:
            raise InvalidGameSessionSettingsError("sets_to_win must be a positive integer")
        if type(is_ranked) is not bool:
            raise InvalidGameSessionSettingsError("is_ranked must be a boolean")
