"""Authenticated standalone match API."""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.matches.application import MatchService
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.dependencies import (
    get_match_repository,
    get_match_service,
    get_matches_uow,
)
from pingstats.modules.matches.domain import MatchContext, MatchStatus
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.matches.infrastructure.uow import SqlAlchemyMatchesUnitOfWork
from pingstats.modules.matches.schemas import (
    AddSetRequest,
    CorrectResultRequest,
    CreateMatchRequest,
    FullResultRequest,
    MatchListResponse,
    MatchResponse,
    MatchRevisionListResponse,
    MatchRevisionResponse,
    VoidMatchRequest,
)
from pingstats.modules.ratings.application.match_state import RatingMatchStateService
from pingstats.modules.ratings.dependencies import (
    get_rating_match_state_service,
    get_rating_query_repository,
)
from pingstats.modules.ratings.infrastructure.repository import SqlAlchemyRatingQueryRepository
from pingstats.modules.users.domain import User

matches_router = APIRouter(prefix="/matches", tags=["matches"])

RatingRepositoryDependency = Annotated[
    SqlAlchemyRatingQueryRepository,
    Depends(get_rating_query_repository),
]
RatingStateServiceDependency = Annotated[
    RatingMatchStateService,
    Depends(get_rating_match_state_service),
]


async def _match_responses(
    views: Sequence[MatchView],
    repository: SqlAlchemyRatingQueryRepository,
    service: RatingMatchStateService,
) -> list[MatchResponse]:
    states = await service.get_states(repository, tuple(view.match for view in views))
    return [MatchResponse.from_view(view, rating_state=states[view.match.id]) for view in views]


async def _match_response(
    view: MatchView,
    repository: SqlAlchemyRatingQueryRepository,
    service: RatingMatchStateService,
) -> MatchResponse:
    return (await _match_responses((view,), repository, service))[0]


@matches_router.post("", response_model=MatchResponse, status_code=status.HTTP_201_CREATED)
async def create_match(
    payload: CreateMatchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view = await service.create_standalone(
        uow,
        creator_id=current_user.id,
        invited_user_id=payload.invited_user_id,
        points_to_win=payload.points_to_win,
        sets_to_win=payload.sets_to_win,
        is_ranked=payload.is_ranked,
    )
    return await _match_response(view, rating_repository, rating_states)


@matches_router.get("", response_model=MatchListResponse)
async def list_matches(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyMatchRepository, Depends(get_match_repository)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
    match_status: Annotated[MatchStatus | None, Query(alias="status")] = None,
    context: MatchContext | None = None,
    is_ranked: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchListResponse:
    items, total = await service.list_matches(
        repository,
        current_user.id,
        status=match_status,
        context=context,
        is_ranked=is_ranked,
        limit=limit,
        offset=offset,
    )
    return MatchListResponse(
        items=await _match_responses(items, rating_repository, rating_states),
        total=total,
        limit=limit,
        offset=offset,
    )


@matches_router.get("/{match_id}", response_model=MatchResponse)
async def get_match(
    match_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyMatchRepository, Depends(get_match_repository)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view = await service.get_match(repository, actor_id=current_user.id, match_id=match_id)
    return await _match_response(view, rating_repository, rating_states)


async def _transition(
    action: str,
    match_id: UUID,
    current_user: User,
    uow: SqlAlchemyMatchesUnitOfWork,
    service: MatchService,
    rating_repository: SqlAlchemyRatingQueryRepository,
    rating_states: RatingMatchStateService,
) -> MatchResponse:
    handler = {"accept": service.accept, "reject": service.reject, "cancel": service.cancel}[action]
    view = await handler(uow, actor_id=current_user.id, match_id=match_id)
    return await _match_response(view, rating_repository, rating_states)


@matches_router.post("/{match_id}/accept", response_model=MatchResponse)
async def accept_match(
    match_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    return await _transition(
        "accept", match_id, current_user, uow, service, rating_repository, rating_states
    )


@matches_router.post("/{match_id}/reject", response_model=MatchResponse)
async def reject_match(
    match_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    return await _transition(
        "reject", match_id, current_user, uow, service, rating_repository, rating_states
    )


@matches_router.post("/{match_id}/cancel", response_model=MatchResponse)
async def cancel_match(
    match_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    return await _transition(
        "cancel", match_id, current_user, uow, service, rating_repository, rating_states
    )


@matches_router.post("/{match_id}/sets", response_model=MatchResponse)
async def add_match_set(
    match_id: UUID,
    payload: AddSetRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view = await service.add_set(
        uow,
        actor_id=current_user.id,
        match_id=match_id,
        score=payload.to_domain(),
        expected_version=payload.expected_version,
    )
    return await _match_response(view, rating_repository, rating_states)


@matches_router.put("/{match_id}/results", response_model=MatchResponse)
async def record_match_result(
    match_id: UUID,
    payload: FullResultRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view = await service.record_full_result(
        uow,
        actor_id=current_user.id,
        match_id=match_id,
        scores=[item.to_domain() for item in payload.sets],
        expected_version=payload.expected_version,
    )
    return await _match_response(view, rating_repository, rating_states)


@matches_router.patch("/{match_id}/results", response_model=MatchResponse)
async def correct_match_result(
    match_id: UUID,
    payload: CorrectResultRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view, _ = await service.correct_result(
        uow,
        actor_id=current_user.id,
        match_id=match_id,
        scores=[item.to_domain() for item in payload.sets],
        expected_version=payload.expected_version,
        reason=payload.reason,
    )
    return await _match_response(view, rating_repository, rating_states)


@matches_router.post("/{match_id}/void", response_model=MatchResponse)
async def void_match(
    match_id: UUID,
    payload: VoidMatchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    uow: Annotated[SqlAlchemyMatchesUnitOfWork, Depends(get_matches_uow)],
    service: Annotated[MatchService, Depends(get_match_service)],
    rating_repository: RatingRepositoryDependency,
    rating_states: RatingStateServiceDependency,
) -> MatchResponse:
    view, _ = await service.void_match(
        uow,
        actor_id=current_user.id,
        match_id=match_id,
        expected_version=payload.expected_version,
        reason=payload.reason,
    )
    return await _match_response(view, rating_repository, rating_states)


@matches_router.get("/{match_id}/revisions", response_model=MatchRevisionListResponse)
async def list_match_revisions(
    match_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyMatchRepository, Depends(get_match_repository)],
    service: Annotated[MatchService, Depends(get_match_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchRevisionListResponse:
    items, total = await service.list_revisions(
        repository,
        actor_id=current_user.id,
        match_id=match_id,
        limit=limit,
        offset=offset,
    )
    return MatchRevisionListResponse(
        items=[MatchRevisionResponse.from_domain(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
