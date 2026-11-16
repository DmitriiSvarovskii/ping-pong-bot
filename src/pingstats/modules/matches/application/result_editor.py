"""Shared audited editing of completed match results."""

from collections.abc import Sequence
from uuid import UUID

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import (
    MatchStateError,
)
from pingstats.modules.matches.application.ports import MatchRepository
from pingstats.modules.matches.application.result_writer import MatchResultWriter
from pingstats.modules.matches.domain import (
    MatchResultRevision,
    MatchRevisionOperation,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)


class MatchResultEditor:
    """Mutate an already locked completed match and append its audit revision."""

    def __init__(self, result_writer: MatchResultWriter | None = None) -> None:
        self._result_writer = result_writer or MatchResultWriter()

    async def correct(
        self,
        repository: MatchRepository,
        view: MatchView,
        *,
        actor_id: UUID,
        scores: Sequence[SetScore],
        reason: str | None,
    ) -> MatchResultRevision:
        self._require_editable(view)
        winner_position = self._result_writer.winner(view, scores, require_complete=True)
        previous = self._snapshot(view)
        new = ResultSnapshot(
            sets=tuple(scores),
            winner_id=view.user_id_at(winner_position),
            status=MatchStatus.COMPLETED,
            completed_at=view.match.completed_at,
            match_version=view.match.version + 1,
        )
        await repository.replace_sets(view.match.id, scores)
        await repository.update_match(view.match, winner_id=new.winner_id)
        return await repository.create_revision(
            match_id=view.match.id,
            changed_by=actor_id,
            previous_result=previous,
            new_result=new,
            reason=reason.strip() if reason else None,
            operation=MatchRevisionOperation.CORRECTION,
        )

    async def void(
        self,
        repository: MatchRepository,
        view: MatchView,
        *,
        actor_id: UUID,
        reason: str | None,
    ) -> MatchResultRevision:
        self._require_editable(view)
        previous = self._snapshot(view)
        await repository.void_completed_match(view.match)
        new = ResultSnapshot(
            sets=previous.sets,
            winner_id=None,
            status=MatchStatus.VOIDED,
            completed_at=view.match.completed_at,
            match_version=view.match.version + 1,
        )
        return await repository.create_revision(
            match_id=view.match.id,
            changed_by=actor_id,
            previous_result=previous,
            new_result=new,
            reason=reason.strip() if reason else None,
            operation=MatchRevisionOperation.VOID,
        )

    @staticmethod
    def _require_editable(view: MatchView) -> None:
        if view.match.status is not MatchStatus.COMPLETED:
            raise MatchStateError("Only a completed match can be corrected or voided")

    @staticmethod
    def _snapshot(view: MatchView) -> ResultSnapshot:
        return ResultSnapshot(
            sets=tuple(item.score() for item in view.sets),
            winner_id=view.match.winner_id,
            status=view.match.status,
            completed_at=view.match.completed_at,
            match_version=view.match.version,
        )
