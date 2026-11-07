"""Application read models for matches."""

from dataclasses import dataclass

from pingstats.modules.matches.domain import Match, MatchParticipant, MatchSet
from pingstats.modules.users.domain import User

ParticipantItem = tuple[MatchParticipant, User]


@dataclass(frozen=True, slots=True)
class MatchView:
    match: Match
    participants: tuple[ParticipantItem, ...]
    sets: tuple[MatchSet, ...]

    def participant_ids(self) -> set:
        return {participant.user_id for participant, _ in self.participants}

    def user_id_at(self, position: int):
        for participant, _ in self.participants:
            if participant.position == position:
                return participant.user_id
        raise RuntimeError(f"Match has no participant at position {position}")
