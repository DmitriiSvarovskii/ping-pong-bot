"""Framework-independent match entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class MatchContext(StrEnum):
    STANDALONE = "standalone"
    GAME_SESSION = "game_session"
    TOURNAMENT = "tournament"


class MatchStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    VOIDED = "voided"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class MatchRevisionOperation(StrEnum):
    CORRECTION = "correction"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class SetScore:
    player1_score: int
    player2_score: int


@dataclass(frozen=True, slots=True)
class Match:
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
    game_session_id: UUID | None = None
    sequence_number: int | None = None


@dataclass(frozen=True, slots=True)
class MatchParticipant:
    id: UUID
    match_id: UUID
    user_id: UUID
    position: int


@dataclass(frozen=True, slots=True)
class MatchSet:
    id: UUID
    match_id: UUID
    set_number: int
    player1_score: int
    player2_score: int
    created_at: datetime

    def score(self) -> SetScore:
        return SetScore(self.player1_score, self.player2_score)


@dataclass(frozen=True, slots=True)
class ResultSnapshot:
    sets: tuple[SetScore, ...]
    winner_id: UUID | None
    status: MatchStatus | None = None
    completed_at: datetime | None = None
    match_version: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "sets": [
                {
                    "set_number": number,
                    "player1_score": score.player1_score,
                    "player2_score": score.player2_score,
                }
                for number, score in enumerate(self.sets, start=1)
            ],
            "winner_id": str(self.winner_id) if self.winner_id is not None else None,
            "status": self.status.value if self.status is not None else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at is not None else None
            ),
            "match_version": self.match_version,
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> "ResultSnapshot":
        raw_sets = value["sets"]
        if not isinstance(raw_sets, list):
            raise ValueError("Result snapshot sets must be a list")
        return cls(
            sets=tuple(
                SetScore(
                    player1_score=int(item["player1_score"]),
                    player2_score=int(item["player2_score"]),
                )
                for item in raw_sets
                if isinstance(item, dict)
            ),
            winner_id=(
                UUID(str(value["winner_id"])) if value.get("winner_id") is not None else None
            ),
            status=(MatchStatus(str(value["status"])) if value.get("status") is not None else None),
            completed_at=(
                datetime.fromisoformat(str(value["completed_at"]))
                if value.get("completed_at") is not None
                else None
            ),
            match_version=(
                int(value["match_version"]) if value.get("match_version") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class MatchResultRevision:
    id: UUID
    match_id: UUID
    revision_number: int
    changed_by: UUID
    previous_result: ResultSnapshot
    new_result: ResultSnapshot
    reason: str | None
    created_at: datetime
    operation: MatchRevisionOperation | None = None
