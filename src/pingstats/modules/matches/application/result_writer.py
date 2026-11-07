"""Shared match-result writer for standalone and contextual workflows."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import InvalidScoreError
from pingstats.modules.matches.application.ports import MatchRepository
from pingstats.modules.matches.domain import (
    InvalidMatchResultError,
    MatchStatus,
    ScoreValidator,
    SetScore,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MatchResultWriter:
    """Apply validated scores without owning authorization, locks, or commit."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def add_set(
        self,
        repository: MatchRepository,
        view: MatchView,
        score: SetScore,
    ) -> MatchView:
        scores = [item.score() for item in view.sets]
        scores.append(score)
        winner_position = self.winner(view, scores, require_complete=False)
        await repository.add_set(view.match.id, len(scores), score)
        if winner_position is None:
            await repository.update_match(view.match)
        else:
            await repository.update_match(
                view.match,
                status=MatchStatus.COMPLETED,
                winner_id=view.user_id_at(winner_position),
                completed_at=self._clock(),
            )
        return await self._required_view(repository, view.match.id)

    async def record_full_result(
        self,
        repository: MatchRepository,
        view: MatchView,
        scores: Sequence[SetScore],
    ) -> MatchView:
        winner_position = self.winner(view, scores, require_complete=True)
        await repository.replace_sets(view.match.id, scores)
        await repository.update_match(
            view.match,
            status=MatchStatus.COMPLETED,
            winner_id=view.user_id_at(winner_position),
            completed_at=self._clock(),
        )
        return await self._required_view(repository, view.match.id)

    @staticmethod
    def winner(
        view: MatchView, scores: Sequence[SetScore], *, require_complete: bool
    ) -> int | None:
        try:
            return ScoreValidator.match_winner(
                scores,
                points_to_win=view.match.points_to_win,
                win_by=view.match.win_by,
                sets_to_win=view.match.sets_to_win,
                require_complete=require_complete,
            )
        except InvalidMatchResultError as error:
            raise InvalidScoreError(str(error)) from error

    @staticmethod
    async def _required_view(repository: MatchRepository, match_id) -> MatchView:
        view = await repository.get_view(match_id)
        if view is None:
            raise RuntimeError("Updated match could not be loaded")
        return view
