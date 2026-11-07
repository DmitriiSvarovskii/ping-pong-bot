"""Standalone match lifecycle and result use cases."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import (
    CorrectionWindowExpiredError,
    InvalidScoreError,
    MatchNotFoundError,
    MatchPermissionError,
    MatchStateError,
    MatchUserNotFoundError,
    MatchVersionConflictError,
    SelfMatchError,
)
from pingstats.modules.matches.application.ports import MatchesUnitOfWork, MatchRepository
from pingstats.modules.matches.application.result_writer import MatchResultWriter
from pingstats.modules.matches.domain import (
    InvalidMatchResultError,
    MatchContext,
    MatchResultRevision,
    MatchStatus,
    ResultSnapshot,
    ScoreValidator,
    SetScore,
)


class MatchService:
    """Coordinate match commands under a row lock and explicit UoW."""

    def __init__(self, *, result_writer: MatchResultWriter | None = None) -> None:
        self._result_writer = result_writer or MatchResultWriter()

    async def create_standalone(
        self,
        uow: MatchesUnitOfWork,
        *,
        creator_id: UUID,
        invited_user_id: UUID,
        points_to_win: int = 11,
        sets_to_win: int = 3,
        is_ranked: bool = True,
    ) -> MatchView:
        if creator_id == invited_user_id:
            raise SelfMatchError
        self._validate_settings(points_to_win, 2, sets_to_win)
        async with uow:
            users = await uow.matches.lock_user_pair(creator_id, invited_user_id)
            if users != {creator_id, invited_user_id}:
                raise MatchUserNotFoundError
            view = await uow.matches.create_standalone(
                created_by=creator_id,
                invited_user_id=invited_user_id,
                points_to_win=points_to_win,
                win_by=2,
                sets_to_win=sets_to_win,
                is_ranked=is_ranked,
            )
            await uow.commit()
        return view

    async def accept(self, uow: MatchesUnitOfWork, *, actor_id: UUID, match_id: UUID) -> MatchView:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_invited(view, actor_id)
            self._require_status(view, MatchStatus.PENDING)
            await uow.matches.update_match(
                view.match, status=MatchStatus.ACTIVE, started_at=datetime.now(UTC)
            )
            await uow.commit()
            result = await self._required_view(uow.matches, match_id)
        return result

    async def reject(self, uow: MatchesUnitOfWork, *, actor_id: UUID, match_id: UUID) -> MatchView:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_invited(view, actor_id)
            self._require_status(view, MatchStatus.PENDING)
            await uow.matches.update_match(view.match, status=MatchStatus.REJECTED)
            await uow.commit()
            result = await self._required_view(uow.matches, match_id)
        return result

    async def cancel(self, uow: MatchesUnitOfWork, *, actor_id: UUID, match_id: UUID) -> MatchView:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_standalone(view)
            if view.match.status is MatchStatus.PENDING:
                if view.match.created_by != actor_id:
                    raise MatchPermissionError("Only the creator can cancel a pending match")
            elif view.match.status is MatchStatus.ACTIVE:
                self._require_participant(view, actor_id)
                if view.sets:
                    raise MatchStateError("A match with recorded sets cannot be cancelled")
            else:
                raise MatchStateError
            await uow.matches.update_match(
                view.match,
                status=MatchStatus.CANCELLED,
                cancelled_at=datetime.now(UTC),
            )
            await uow.commit()
            result = await self._required_view(uow.matches, match_id)
        return result

    async def add_set(
        self,
        uow: MatchesUnitOfWork,
        *,
        actor_id: UUID,
        match_id: UUID,
        score: SetScore,
        expected_version: int,
    ) -> MatchView:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_standalone(view)
            self._require_participant(view, actor_id)
            self._require_status(view, MatchStatus.ACTIVE)
            self._require_version(view, expected_version)
            result = await self._result_writer.add_set(uow.matches, view, score)
            await uow.commit()
        return result

    async def record_full_result(
        self,
        uow: MatchesUnitOfWork,
        *,
        actor_id: UUID,
        match_id: UUID,
        scores: Sequence[SetScore],
        expected_version: int | None = None,
    ) -> MatchView:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_standalone(view)
            self._require_participant(view, actor_id)
            if view.match.status is MatchStatus.COMPLETED:
                if tuple(item.score() for item in view.sets) == tuple(scores):
                    return view
                raise MatchStateError("Completed result can only be changed through correction")
            self._require_status(view, MatchStatus.ACTIVE)
            if expected_version is not None:
                self._require_version(view, expected_version)
            result = await self._result_writer.record_full_result(uow.matches, view, scores)
            await uow.commit()
        return result

    async def correct_result(
        self,
        uow: MatchesUnitOfWork,
        *,
        actor_id: UUID,
        match_id: UUID,
        scores: Sequence[SetScore],
        expected_version: int,
        reason: str | None,
        now: datetime | None = None,
    ) -> tuple[MatchView, MatchResultRevision]:
        changed_at = now or datetime.now(UTC)
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_participant(view, actor_id)
            self._require_status(view, MatchStatus.COMPLETED)
            self._require_version(view, expected_version)
            if view.match.context is not MatchContext.STANDALONE:
                raise MatchStateError("This correction policy only supports standalone matches")
            if view.match.completed_at is None or changed_at > view.match.completed_at + timedelta(
                hours=24
            ):
                raise CorrectionWindowExpiredError
            winner_position = self._result_writer.winner(view, scores, require_complete=True)
            old_winner = view.match.winner_id
            if old_winner is None:
                raise MatchStateError
            previous = ResultSnapshot(
                sets=tuple(item.score() for item in view.sets), winner_id=old_winner
            )
            new = ResultSnapshot(sets=tuple(scores), winner_id=view.user_id_at(winner_position))
            await uow.matches.replace_sets(match_id, scores)
            await uow.matches.update_match(view.match, winner_id=new.winner_id)
            revision = await uow.matches.create_revision(
                match_id=match_id,
                changed_by=actor_id,
                previous_result=previous,
                new_result=new,
                reason=reason.strip() if reason else None,
            )
            await uow.commit()
            result = await self._required_view(uow.matches, match_id)
        return result, revision

    async def get_match(
        self, repository: MatchRepository, *, actor_id: UUID, match_id: UUID
    ) -> MatchView:
        view = await repository.get_view(match_id)
        if view is None:
            raise MatchNotFoundError
        self._require_participant(view, actor_id)
        return view

    async def list_matches(self, repository: MatchRepository, user_id: UUID, **filters):
        return await repository.list_for_user(user_id, **filters)

    async def list_revisions(
        self,
        repository: MatchRepository,
        *,
        actor_id: UUID,
        match_id: UUID,
        limit: int,
        offset: int,
    ):
        await self.get_match(repository, actor_id=actor_id, match_id=match_id)
        return await repository.list_revisions(match_id, limit=limit, offset=offset)

    async def _locked_view(self, repository: MatchRepository, match_id: UUID) -> MatchView:
        view = await repository.get_view(match_id, for_update=True)
        if view is None:
            raise MatchNotFoundError
        return view

    async def _required_view(self, repository: MatchRepository, match_id: UUID) -> MatchView:
        view = await repository.get_view(match_id)
        if view is None:
            raise MatchNotFoundError
        return view

    @staticmethod
    def _require_participant(view: MatchView, actor_id: UUID) -> None:
        if actor_id not in view.participant_ids():
            raise MatchPermissionError("Only a match participant may perform this operation")

    @staticmethod
    def _require_invited(view: MatchView, actor_id: UUID) -> None:
        if view.user_id_at(2) != actor_id:
            raise MatchPermissionError("Only the invited player may respond")

    @staticmethod
    def _require_status(view: MatchView, expected: MatchStatus) -> None:
        if view.match.status is not expected:
            raise MatchStateError

    @staticmethod
    def _require_standalone(view: MatchView) -> None:
        if view.match.context is not MatchContext.STANDALONE:
            raise MatchStateError("Session matches must use the game session workflow")

    @staticmethod
    def _require_version(view: MatchView, expected_version: int) -> None:
        if view.match.version != expected_version:
            raise MatchVersionConflictError

    @staticmethod
    def _validate_settings(points_to_win: int, win_by: int, sets_to_win: int) -> None:
        try:
            ScoreValidator.validate_settings(
                points_to_win=points_to_win, win_by=win_by, sets_to_win=sets_to_win
            )
        except InvalidMatchResultError as exc:
            raise InvalidScoreError(str(exc)) from exc
