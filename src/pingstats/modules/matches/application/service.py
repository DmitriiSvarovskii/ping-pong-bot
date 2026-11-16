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
from pingstats.modules.matches.application.result_editor import MatchResultEditor
from pingstats.modules.matches.application.result_writer import MatchResultWriter
from pingstats.modules.matches.domain import (
    InvalidMatchResultError,
    MatchContext,
    MatchResultRevision,
    MatchStatus,
    ScoreValidator,
    SetScore,
)
from pingstats.modules.ratings.application import (
    RatingCorrectionCoordinator,
    RatingMatchInputRecorder,
)
from pingstats.modules.ratings.application.ports import (
    NullRatingRebuildNotifier,
    RatingRebuildNotifier,
)


class MatchService:
    """Coordinate match commands under a row lock and explicit UoW."""

    def __init__(
        self,
        *,
        result_writer: MatchResultWriter | None = None,
        rating_input_recorder: RatingMatchInputRecorder | None = None,
        result_editor: MatchResultEditor | None = None,
        rating_correction_coordinator: RatingCorrectionCoordinator | None = None,
        rating_rebuild_notifier: RatingRebuildNotifier | None = None,
    ) -> None:
        self._result_writer = result_writer or MatchResultWriter()
        self._rating_input_recorder = rating_input_recorder or RatingMatchInputRecorder()
        self._result_editor = result_editor or MatchResultEditor(self._result_writer)
        self._rating_correction_coordinator = (
            rating_correction_coordinator or RatingCorrectionCoordinator()
        )
        self._rating_rebuild_notifier = rating_rebuild_notifier or NullRatingRebuildNotifier()

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
            if result.match.status is MatchStatus.COMPLETED:
                await self._rating_input_recorder.record(
                    uow.rating_periods, uow.rating_inputs, result
                )
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
            await self._rating_input_recorder.record(uow.rating_periods, uow.rating_inputs, result)
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
            self._require_standalone(view)
            if view.match.created_by != actor_id:
                raise MatchPermissionError("Only the match organizer can correct its result")
            self._require_status(view, MatchStatus.COMPLETED)
            self._require_version(view, expected_version)
            if view.match.completed_at is None or changed_at > view.match.completed_at + timedelta(
                hours=24
            ):
                raise CorrectionWindowExpiredError
            revision = await self._result_editor.correct(
                uow.matches, view, actor_id=actor_id, scores=scores, reason=reason
            )
            result = await self._required_view(uow.matches, match_id)
            await self._coordinate_rating_correction(uow, result, revision)
            await uow.commit()
        return result, revision

    async def void_match(
        self,
        uow: MatchesUnitOfWork,
        *,
        actor_id: UUID,
        match_id: UUID,
        expected_version: int,
        reason: str | None,
    ) -> tuple[MatchView, MatchResultRevision]:
        async with uow:
            view = await self._locked_view(uow.matches, match_id)
            self._require_standalone(view)
            if view.match.created_by != actor_id:
                raise MatchPermissionError("Only the match organizer can void it")
            self._require_status(view, MatchStatus.COMPLETED)
            self._require_version(view, expected_version)
            revision = await self._result_editor.void(
                uow.matches, view, actor_id=actor_id, reason=reason
            )
            result = await self._required_view(uow.matches, match_id)
            await self._coordinate_rating_correction(uow, result, revision)
            await uow.commit()
        return result, revision

    async def _coordinate_rating_correction(
        self,
        uow: MatchesUnitOfWork,
        result: MatchView,
        revision: MatchResultRevision,
    ) -> None:
        if not result.match.is_ranked:
            return
        rebuild_created = await self._rating_correction_coordinator.coordinate(
            periods=uow.rating_periods,
            inputs=uow.rating_inputs,
            recalculations=uow.rating_recalculations,
            rebuild_jobs=uow.rating_rebuild_jobs,
            rebuild_periods=uow.rating_rebuild_periods,
            match=result,
            revision=revision,
        )
        if rebuild_created:
            uow.add_after_commit("rating.rebuild", self._rating_rebuild_notifier.notify)

    async def get_match(
        self, repository: MatchRepository, *, actor_id: UUID, match_id: UUID
    ) -> MatchView:
        view = await repository.get_view(match_id)
        if view is None:
            raise MatchNotFoundError
        if actor_id not in view.participant_ids() and actor_id != view.match.created_by:
            raise MatchPermissionError("Only a participant or match organizer may view it")
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
