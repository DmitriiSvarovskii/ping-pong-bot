"""Pydantic 2 transport schemas for matches."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StrictInt

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    MatchContext,
    MatchResultRevision,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)
from pingstats.modules.users.schemas import UserPublicResponse

ScoreValue = Annotated[StrictInt, Field(ge=0, le=32767)]


class SetScoreInput(BaseModel):
    player1_score: ScoreValue
    player2_score: ScoreValue

    def to_domain(self) -> SetScore:
        return SetScore(self.player1_score, self.player2_score)


class CreateMatchRequest(BaseModel):
    invited_user_id: UUID
    points_to_win: Annotated[StrictInt, Field(ge=1, le=32767)] = 11
    sets_to_win: Annotated[StrictInt, Field(ge=1, le=99)] = 3
    is_ranked: bool = True


class AddSetRequest(SetScoreInput):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class FullResultRequest(BaseModel):
    sets: Annotated[list[SetScoreInput], Field(min_length=1, max_length=197)]
    expected_version: Annotated[StrictInt, Field(ge=1)] | None = None


class CorrectResultRequest(FullResultRequest):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    reason: Annotated[str | None, Field(max_length=1000)] = None


class MatchParticipantResponse(BaseModel):
    position: int
    user: UserPublicResponse


class MatchSetResponse(BaseModel):
    id: UUID
    set_number: int
    player1_score: int
    player2_score: int
    created_at: datetime


class MatchResponse(BaseModel):
    id: UUID
    context: MatchContext
    status: MatchStatus
    created_by: UUID
    points_to_win: int
    win_by: int
    sets_to_win: int
    is_ranked: bool
    winner_id: UUID | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    version: int
    participants: list[MatchParticipantResponse]
    sets: list[MatchSetResponse]

    @classmethod
    def from_view(cls, view: MatchView) -> "MatchResponse":
        match = view.match
        return cls(
            id=match.id,
            context=match.context,
            status=match.status,
            created_by=match.created_by,
            points_to_win=match.points_to_win,
            win_by=match.win_by,
            sets_to_win=match.sets_to_win,
            is_ranked=match.is_ranked,
            winner_id=match.winner_id,
            created_at=match.created_at,
            updated_at=match.updated_at,
            started_at=match.started_at,
            completed_at=match.completed_at,
            cancelled_at=match.cancelled_at,
            version=match.version,
            participants=[
                MatchParticipantResponse(
                    position=participant.position,
                    user=UserPublicResponse.from_domain(user),
                )
                for participant, user in view.participants
            ],
            sets=[
                MatchSetResponse(
                    id=item.id,
                    set_number=item.set_number,
                    player1_score=item.player1_score,
                    player2_score=item.player2_score,
                    created_at=item.created_at,
                )
                for item in view.sets
            ],
        )


class MatchListResponse(BaseModel):
    items: list[MatchResponse]
    total: int
    limit: int
    offset: int


class ResultSnapshotResponse(BaseModel):
    sets: list[SetScoreInput]
    winner_id: UUID

    @classmethod
    def from_domain(cls, snapshot: ResultSnapshot) -> "ResultSnapshotResponse":
        return cls(
            sets=[
                SetScoreInput(
                    player1_score=item.player1_score,
                    player2_score=item.player2_score,
                )
                for item in snapshot.sets
            ],
            winner_id=snapshot.winner_id,
        )


class MatchRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
    changed_by: UUID
    previous_result: ResultSnapshotResponse
    new_result: ResultSnapshotResponse
    reason: str | None
    created_at: datetime

    @classmethod
    def from_domain(cls, revision: MatchResultRevision) -> "MatchRevisionResponse":
        return cls(
            id=revision.id,
            revision_number=revision.revision_number,
            changed_by=revision.changed_by,
            previous_result=ResultSnapshotResponse.from_domain(revision.previous_result),
            new_result=ResultSnapshotResponse.from_domain(revision.new_result),
            reason=revision.reason,
            created_at=revision.created_at,
        )


class MatchRevisionListResponse(BaseModel):
    items: list[MatchRevisionResponse]
    total: int
    limit: int
    offset: int
