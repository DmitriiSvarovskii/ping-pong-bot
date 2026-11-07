"""Authenticated HTTP API for free game sessions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.game_sessions.application import GameSessionService
from pingstats.modules.game_sessions.dependencies import (
    get_game_session_match_repository,
    get_game_session_repository,
    get_game_session_service,
    get_game_sessions_uow,
)
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.game_sessions.infrastructure.uow import SqlAlchemyGameSessionsUnitOfWork
from pingstats.modules.game_sessions.schemas import (
    CurrentSessionMatchRequest,
    SessionCreateRequest,
    SessionListResponse,
    SessionMatchCommandResponse,
    SessionMatchListResponse,
    SessionMatchResponse,
    SessionParticipantListResponse,
    SessionParticipantResponse,
    SessionQueueResponse,
    SessionResponse,
    SessionResultRequest,
    SessionSetRequest,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.users.domain import User

game_sessions_router = APIRouter(
    prefix="/game-sessions",
    tags=["game-sessions"],
    responses={
        400: {"description": "Invalid score or session parameters"},
        401: {"description": "Invalid or missing bearer token"},
        403: {"description": "The authenticated user is not allowed to perform the operation"},
        404: {"description": "Game session or current match was not found"},
        409: {"description": "Session state, current match, or version conflict"},
    },
)


@game_sessions_router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    view = await service.create_session(
        uow,
        actor_id=current_user.id,
        name=payload.name,
        points_to_win=payload.points_to_win,
        sets_to_win=payload.sets_to_win,
        is_ranked=payload.is_ranked,
    )
    return SessionResponse.from_view(view)


@game_sessions_router.get("", response_model=SessionListResponse)
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionListResponse:
    items, total = await service.list_sessions(
        repository, actor_id=current_user.id, limit=limit, offset=offset
    )
    return SessionListResponse(
        items=[SessionResponse.from_view(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@game_sessions_router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    view = await service.get_session(repository, actor_id=current_user.id, session_id=session_id)
    return SessionResponse.from_view(view)


async def _session_transition(
    action: str,
    session_id: UUID,
    current_user: User,
    uow: SqlAlchemyGameSessionsUnitOfWork,
    service: GameSessionService,
) -> SessionResponse:
    handler = {
        "join": service.join_session,
        "leave": service.leave_session,
        "start": service.start_session,
        "complete": service.complete_session,
        "cancel": service.cancel_session,
    }[action]
    view = await handler(uow, actor_id=current_user.id, session_id=session_id)
    return SessionResponse.from_view(view)


@game_sessions_router.post("/{session_id}/join", response_model=SessionResponse)
async def join_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    return await _session_transition("join", session_id, current_user, uow, service)


@game_sessions_router.post("/{session_id}/leave", response_model=SessionResponse)
async def leave_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    return await _session_transition("leave", session_id, current_user, uow, service)


@game_sessions_router.post("/{session_id}/start", response_model=SessionResponse)
async def start_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    return await _session_transition("start", session_id, current_user, uow, service)


@game_sessions_router.post("/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    return await _session_transition("complete", session_id, current_user, uow, service)


@game_sessions_router.post("/{session_id}/cancel", response_model=SessionResponse)
async def cancel_session(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionResponse:
    return await _session_transition("cancel", session_id, current_user, uow, service)


@game_sessions_router.get(
    "/{session_id}/participants", response_model=SessionParticipantListResponse
)
async def list_participants(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionParticipantListResponse:
    items, total = await service.list_participants(
        repository,
        actor_id=current_user.id,
        session_id=session_id,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return SessionParticipantListResponse(
        items=[SessionParticipantResponse.from_item(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@game_sessions_router.get("/{session_id}/queue", response_model=SessionQueueResponse)
async def get_queue(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionQueueResponse:
    view = await service.get_queue(repository, actor_id=current_user.id, session_id=session_id)
    return SessionQueueResponse.from_view(view)


@game_sessions_router.get("/{session_id}/matches", response_model=SessionMatchListResponse)
async def list_session_matches(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    sessions: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    matches: Annotated[SqlAlchemyMatchRepository, Depends(get_game_session_match_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionMatchListResponse:
    items, total = await service.list_session_matches(
        sessions,
        matches,
        actor_id=current_user.id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return SessionMatchListResponse(
        items=[SessionMatchResponse.from_view(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@game_sessions_router.get("/{session_id}/matches/current", response_model=SessionMatchResponse)
async def get_current_match(
    session_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    sessions: Annotated[SqlAlchemyGameSessionRepository, Depends(get_game_session_repository)],
    matches: Annotated[SqlAlchemyMatchRepository, Depends(get_game_session_match_repository)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionMatchResponse:
    view = await service.get_current_session_match(
        sessions, matches, actor_id=current_user.id, session_id=session_id
    )
    return SessionMatchResponse.from_view(view)


@game_sessions_router.post(
    "/{session_id}/matches/current/sets", response_model=SessionMatchCommandResponse
)
async def add_current_match_set(
    session_id: UUID,
    payload: SessionSetRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionMatchCommandResponse:
    result = await service.record_session_set(
        uow,
        actor_id=current_user.id,
        session_id=session_id,
        match_id=payload.match_id,
        score=payload.to_domain(),
        expected_version=payload.expected_version,
    )
    return SessionMatchCommandResponse.from_result(result)


@game_sessions_router.put(
    "/{session_id}/matches/current/result", response_model=SessionMatchCommandResponse
)
async def record_current_match_result(
    session_id: UUID,
    payload: SessionResultRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionMatchCommandResponse:
    result = await service.record_session_result(
        uow,
        actor_id=current_user.id,
        session_id=session_id,
        match_id=payload.match_id,
        scores=[item.to_domain() for item in payload.sets],
        expected_version=payload.expected_version,
    )
    return SessionMatchCommandResponse.from_result(result)


async def _cancel_current_match(
    *,
    leave: bool,
    session_id: UUID,
    payload: CurrentSessionMatchRequest,
    current_user: User,
    uow: SqlAlchemyGameSessionsUnitOfWork,
    service: GameSessionService,
) -> SessionMatchCommandResponse:
    handler = service.cancel_current_match_and_leave if leave else service.cancel_current_match
    result = await handler(
        uow,
        actor_id=current_user.id,
        session_id=session_id,
        match_id=payload.match_id,
    )
    return SessionMatchCommandResponse.from_result(result)


@game_sessions_router.post(
    "/{session_id}/matches/current/cancel", response_model=SessionMatchCommandResponse
)
async def cancel_current_match(
    session_id: UUID,
    payload: CurrentSessionMatchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionMatchCommandResponse:
    return await _cancel_current_match(
        leave=False,
        session_id=session_id,
        payload=payload,
        current_user=current_user,
        uow=uow,
        service=service,
    )


@game_sessions_router.post(
    "/{session_id}/matches/current/cancel-and-leave",
    response_model=SessionMatchCommandResponse,
)
async def cancel_current_match_and_leave(
    session_id: UUID,
    payload: CurrentSessionMatchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyGameSessionsUnitOfWork, Depends(get_game_sessions_uow)],
    service: Annotated[GameSessionService, Depends(get_game_session_service)],
) -> SessionMatchCommandResponse:
    return await _cancel_current_match(
        leave=True,
        session_id=session_id,
        payload=payload,
        current_user=current_user,
        uow=uow,
        service=service,
    )
