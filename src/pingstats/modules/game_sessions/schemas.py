"""Pydantic 2 transport schemas for game sessions."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt, model_validator

from pingstats.modules.game_sessions.application.dto import (
    GameSessionMatchResult,
    GameSessionParticipantItem,
    GameSessionPlayerStatistics,
    GameSessionQueueView,
    GameSessionView,
)
from pingstats.modules.game_sessions.domain import GameSessionParticipant, GameSessionStatus
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.schemas import MatchResponse, SetScoreInput
from pingstats.modules.users.schemas import UserPublicResponse


class SessionCreateRequest(BaseModel):
    name: Annotated[str | None, Field(max_length=128)] = None
    points_to_win: Annotated[StrictInt, Field(ge=1, le=32767)] = 11
    sets_to_win: Annotated[StrictInt, Field(ge=1, le=99)] = 1
    is_ranked: bool = True


class SessionParticipantResponse(BaseModel):
    participation_id: UUID
    user: UserPublicResponse
    joined_at: datetime
    left_at: datetime | None
    is_active: bool

    @classmethod
    def from_item(cls, item: GameSessionParticipantItem) -> "SessionParticipantResponse":
        return cls(
            participation_id=item.participation.id,
            user=UserPublicResponse.from_domain(item.user),
            joined_at=item.participation.joined_at,
            left_at=item.participation.left_at,
            is_active=item.participation.is_active,
        )


class SessionResponse(BaseModel):
    id: UUID
    organizer_id: UUID
    status: GameSessionStatus
    name: str | None
    points_to_win: int
    sets_to_win: int
    is_ranked: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    version: int
    active_participants: list[SessionParticipantResponse]

    @classmethod
    def from_view(cls, view: GameSessionView) -> "SessionResponse":
        session = view.session
        return cls(
            id=session.id,
            organizer_id=session.organizer_id,
            status=session.status,
            name=session.name,
            points_to_win=session.points_to_win,
            sets_to_win=session.sets_to_win,
            is_ranked=session.is_ranked,
            created_at=session.created_at,
            started_at=session.started_at,
            completed_at=session.completed_at,
            version=session.version,
            active_participants=[
                SessionParticipantResponse.from_item(item) for item in view.active_participants
            ],
        )


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    limit: int
    offset: int


class SessionParticipantListResponse(BaseModel):
    items: list[SessionParticipantResponse]
    total: int
    limit: int
    offset: int


class SessionQueueParticipantResponse(BaseModel):
    participation_id: UUID
    user_id: UUID
    joined_at: datetime

    @classmethod
    def from_domain(cls, participant: GameSessionParticipant) -> "SessionQueueParticipantResponse":
        return cls(
            participation_id=participant.id,
            user_id=participant.user_id,
            joined_at=participant.joined_at,
        )


class SessionQueueResponse(BaseModel):
    current_pair: list[SessionQueueParticipantResponse]
    waiting: list[SessionQueueParticipantResponse]
    is_paused: bool

    @classmethod
    def from_view(cls, view: GameSessionQueueView | None) -> "SessionQueueResponse":
        if view is None:
            return cls(current_pair=[], waiting=[], is_paused=True)
        return cls(
            current_pair=[
                SessionQueueParticipantResponse.from_domain(item) for item in view.current_pair
            ],
            waiting=[SessionQueueParticipantResponse.from_domain(item) for item in view.waiting],
            is_paused=False,
        )


class SessionMatchResponse(MatchResponse):
    game_session_id: UUID
    sequence_number: int

    @classmethod
    def from_view(cls, view: MatchView) -> "SessionMatchResponse":
        match = view.match
        if match.game_session_id is None or match.sequence_number is None:
            raise ValueError("Expected a game-session match")
        return cls(
            **MatchResponse.from_view(view).model_dump(),
            game_session_id=match.game_session_id,
            sequence_number=match.sequence_number,
        )


class SessionMatchListResponse(BaseModel):
    items: list[SessionMatchResponse]
    total: int
    limit: int
    offset: int


class SessionPlayerStatisticsResponse(BaseModel):
    user: UserPublicResponse
    wins: int
    losses: int
    points_for: int
    points_against: int
    point_difference: int

    @classmethod
    def from_domain(cls, item: GameSessionPlayerStatistics) -> "SessionPlayerStatisticsResponse":
        return cls(
            user=UserPublicResponse.from_domain(item.user),
            wins=item.wins,
            losses=item.losses,
            points_for=item.points_for,
            points_against=item.points_against,
            point_difference=item.point_difference,
        )


class SessionStatisticsResponse(BaseModel):
    items: list[SessionPlayerStatisticsResponse]


class CurrentSessionMatchRequest(BaseModel):
    match_id: UUID


class SessionSkipRequest(CurrentSessionMatchRequest):
    skipped_player_id: UUID | None = None
    skip_both: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> "SessionSkipRequest":
        if self.skip_both == (self.skipped_player_id is not None):
            raise ValueError("Provide either skipped_player_id or skip_both=true")
        return self


class SessionSetRequest(CurrentSessionMatchRequest, SetScoreInput):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class SessionResultRequest(CurrentSessionMatchRequest):
    sets: Annotated[list[SetScoreInput], Field(min_length=1, max_length=197)]
    expected_version: Annotated[StrictInt, Field(ge=1)] | None = None


class SessionMatchCommandResponse(BaseModel):
    match: SessionMatchResponse
    current_match: SessionMatchResponse | None
    queue: SessionQueueResponse | None

    @classmethod
    def from_result(cls, result: GameSessionMatchResult) -> "SessionMatchCommandResponse":
        return cls(
            match=SessionMatchResponse.from_view(result.match),
            current_match=(
                SessionMatchResponse.from_view(result.current_match)
                if result.current_match is not None
                else None
            ),
            queue=(
                SessionQueueResponse.from_view(result.queue) if result.queue is not None else None
            ),
        )
