"""Read models returned by game session application services."""

from dataclasses import dataclass

from pingstats.modules.game_sessions.domain import GameSession, GameSessionParticipant
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.users.domain import User


@dataclass(frozen=True, slots=True)
class GameSessionParticipantItem:
    participation: GameSessionParticipant
    user: User


@dataclass(frozen=True, slots=True)
class GameSessionView:
    session: GameSession
    active_participants: tuple[GameSessionParticipantItem, ...]


@dataclass(frozen=True, slots=True)
class GameSessionQueueView:
    """Current pair and ordered waiting participants."""

    current_pair: tuple[GameSessionParticipant, GameSessionParticipant]
    waiting: tuple[GameSessionParticipant, ...]


@dataclass(frozen=True, slots=True)
class GameSessionMatchResult:
    """Result of a session-match command and the active successor."""

    match: MatchView
    current_match: MatchView | None
    queue: GameSessionQueueView | None


@dataclass(frozen=True, slots=True)
class GameSessionPlayerStatistics:
    """Derived statistics for one historical participant within one session."""

    user: User
    wins: int
    losses: int
    points_for: int
    points_against: int

    @property
    def point_difference(self) -> int:
        return self.points_for - self.points_against
