"""Matches domain model and score rules."""

from pingstats.modules.matches.domain.entities import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchResultRevision,
    MatchRevisionOperation,
    MatchSet,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)
from pingstats.modules.matches.domain.scoring import InvalidMatchResultError, ScoreValidator

__all__ = [
    "InvalidMatchResultError",
    "Match",
    "MatchContext",
    "MatchParticipant",
    "MatchResultRevision",
    "MatchRevisionOperation",
    "MatchSet",
    "MatchStatus",
    "ResultSnapshot",
    "ScoreValidator",
    "SetScore",
]
