"""Standalone match application lifecycle tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.application.errors import (
    CorrectionWindowExpiredError,
    MatchPermissionError,
    MatchStateError,
    MatchVersionConflictError,
    SelfMatchError,
)
from pingstats.modules.matches.application.service import MatchService
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchResultRevision,
    MatchSet,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)
from pingstats.modules.users.domain import User


def make_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(user_id, None, name, None, name.lower(), None, now, now, None)


class FakeMatchRepository:
    def __init__(self, users: dict[UUID, User]) -> None:
        self.users = users
        self.views: dict[UUID, MatchView] = {}
        self.revisions: dict[UUID, list[MatchResultRevision]] = {}

    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]:
        return {item for item in (first_id, second_id) if item in self.users}

    async def create_standalone(
        self,
        *,
        created_by: UUID,
        invited_user_id: UUID,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> MatchView:
        now = datetime.now(UTC)
        match_id = uuid4()
        match = Match(
            match_id,
            MatchContext.STANDALONE,
            MatchStatus.PENDING,
            created_by,
            points_to_win,
            win_by,
            sets_to_win,
            is_ranked,
            None,
            now,
            now,
            None,
            None,
            None,
            1,
        )
        participants = tuple(
            (
                MatchParticipant(uuid4(), match_id, user_id, position),
                self.users[user_id],
            )
            for position, user_id in ((1, created_by), (2, invited_user_id))
        )
        view = MatchView(match, participants, ())
        self.views[match_id] = view
        return view

    async def get_view(self, match_id: UUID, *, for_update: bool = False) -> MatchView | None:
        del for_update
        return self.views.get(match_id)

    async def update_match(
        self,
        match: Match,
        *,
        status: MatchStatus | None = None,
        winner_id: UUID | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> Match:
        updated = replace(
            match,
            status=status or match.status,
            winner_id=winner_id if winner_id is not None else match.winner_id,
            started_at=started_at if started_at is not None else match.started_at,
            completed_at=completed_at if completed_at is not None else match.completed_at,
            cancelled_at=cancelled_at if cancelled_at is not None else match.cancelled_at,
            updated_at=datetime.now(UTC),
            version=match.version + 1,
        )
        view = self.views[match.id]
        self.views[match.id] = replace(view, match=updated)
        return updated

    async def add_set(self, match_id: UUID, set_number: int, score: SetScore) -> None:
        view = self.views[match_id]
        item = MatchSet(
            uuid4(),
            match_id,
            set_number,
            score.player1_score,
            score.player2_score,
            datetime.now(UTC),
        )
        self.views[match_id] = replace(view, sets=(*view.sets, item))

    async def replace_sets(self, match_id: UUID, scores: list[SetScore]) -> None:
        view = self.views[match_id]
        self.views[match_id] = replace(
            view,
            sets=tuple(
                MatchSet(
                    uuid4(),
                    match_id,
                    number,
                    score.player1_score,
                    score.player2_score,
                    datetime.now(UTC),
                )
                for number, score in enumerate(scores, 1)
            ),
        )

    async def create_revision(
        self,
        *,
        match_id: UUID,
        changed_by: UUID,
        previous_result: ResultSnapshot,
        new_result: ResultSnapshot,
        reason: str | None,
    ) -> MatchResultRevision:
        revisions = self.revisions.setdefault(match_id, [])
        revision = MatchResultRevision(
            uuid4(),
            match_id,
            len(revisions) + 1,
            changed_by,
            previous_result,
            new_result,
            reason,
            datetime.now(UTC),
        )
        revisions.append(revision)
        return revision


class FakeUow:
    def __init__(self, repository: FakeMatchRepository) -> None:
        self.matches = repository

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def setup() -> tuple[MatchService, FakeMatchRepository, UUID, UUID, UUID]:
    creator, invited, outsider = uuid4(), uuid4(), uuid4()
    users = {
        creator: make_user(creator, "Creator"),
        invited: make_user(invited, "Invited"),
        outsider: make_user(outsider, "Outsider"),
    }
    return MatchService(), FakeMatchRepository(users), creator, invited, outsider


async def active_match(
    service: MatchService, repository: FakeMatchRepository, creator: UUID, invited: UUID
) -> MatchView:
    pending = await service.create_standalone(
        FakeUow(repository), creator_id=creator, invited_user_id=invited
    )
    return await service.accept(FakeUow(repository), actor_id=invited, match_id=pending.match.id)


async def test_create_accept_reject_and_cancel(setup) -> None:
    service, repository, creator, invited, outsider = setup
    with pytest.raises(SelfMatchError):
        await service.create_standalone(
            FakeUow(repository), creator_id=creator, invited_user_id=creator
        )
    pending = await service.create_standalone(
        FakeUow(repository), creator_id=creator, invited_user_id=invited
    )
    assert pending.match.status is MatchStatus.PENDING
    assert (pending.match.points_to_win, pending.match.sets_to_win, pending.match.is_ranked) == (
        11,
        3,
        True,
    )
    with pytest.raises(MatchPermissionError):
        await service.accept(FakeUow(repository), actor_id=outsider, match_id=pending.match.id)
    active = await service.accept(FakeUow(repository), actor_id=invited, match_id=pending.match.id)
    assert active.match.status is MatchStatus.ACTIVE

    rejectable = await service.create_standalone(
        FakeUow(repository), creator_id=creator, invited_user_id=invited
    )
    rejected = await service.reject(
        FakeUow(repository), actor_id=invited, match_id=rejectable.match.id
    )
    assert rejected.match.status is MatchStatus.REJECTED

    cancellable = await service.create_standalone(
        FakeUow(repository), creator_id=creator, invited_user_id=invited
    )
    cancelled = await service.cancel(
        FakeUow(repository), actor_id=creator, match_id=cancellable.match.id
    )
    assert cancelled.match.status is MatchStatus.CANCELLED


async def test_incremental_sets_auto_complete_and_version_protects(setup) -> None:
    service, repository, creator, invited, _ = setup
    pending = await service.create_standalone(
        FakeUow(repository),
        creator_id=creator,
        invited_user_id=invited,
        sets_to_win=2,
    )
    active = await service.accept(FakeUow(repository), actor_id=invited, match_id=pending.match.id)
    first = await service.add_set(
        FakeUow(repository),
        actor_id=creator,
        match_id=active.match.id,
        score=SetScore(11, 4),
        expected_version=active.match.version,
    )
    with pytest.raises(MatchVersionConflictError):
        await service.add_set(
            FakeUow(repository),
            actor_id=creator,
            match_id=active.match.id,
            score=SetScore(11, 5),
            expected_version=active.match.version,
        )
    completed = await service.add_set(
        FakeUow(repository),
        actor_id=invited,
        match_id=first.match.id,
        score=SetScore(12, 10),
        expected_version=first.match.version,
    )
    assert completed.match.status is MatchStatus.COMPLETED
    assert completed.match.winner_id == creator
    assert len(completed.sets) == 2
    with pytest.raises(MatchStateError):
        await service.add_set(
            FakeUow(repository),
            actor_id=creator,
            match_id=completed.match.id,
            score=SetScore(11, 1),
            expected_version=completed.match.version,
        )


async def test_full_result_is_complete_idempotent_and_participant_only(setup) -> None:
    service, repository, creator, invited, outsider = setup
    active = await active_match(service, repository, creator, invited)
    scores = [SetScore(11, 3), SetScore(8, 11), SetScore(11, 7), SetScore(11, 9)]
    with pytest.raises(MatchPermissionError):
        await service.record_full_result(
            FakeUow(repository), actor_id=outsider, match_id=active.match.id, scores=scores
        )
    completed = await service.record_full_result(
        FakeUow(repository), actor_id=creator, match_id=active.match.id, scores=scores
    )
    repeated = await service.record_full_result(
        FakeUow(repository), actor_id=invited, match_id=active.match.id, scores=scores
    )
    assert completed.match.status is MatchStatus.COMPLETED
    assert repeated.match.version == completed.match.version
    assert repeated.match.winner_id == creator


async def test_correction_audit_permissions_and_window(setup) -> None:
    service, repository, creator, invited, outsider = setup
    active = await active_match(service, repository, creator, invited)
    completed = await service.record_full_result(
        FakeUow(repository),
        actor_id=creator,
        match_id=active.match.id,
        scores=[SetScore(11, 3), SetScore(11, 4), SetScore(11, 5)],
    )
    corrected_scores = [SetScore(5, 11), SetScore(6, 11), SetScore(7, 11)]
    with pytest.raises(MatchPermissionError):
        await service.correct_result(
            FakeUow(repository),
            actor_id=outsider,
            match_id=completed.match.id,
            scores=corrected_scores,
            expected_version=completed.match.version,
            reason=None,
        )
    corrected, revision = await service.correct_result(
        FakeUow(repository),
        actor_id=invited,
        match_id=completed.match.id,
        scores=corrected_scores,
        expected_version=completed.match.version,
        reason=" score fixed ",
    )
    assert corrected.match.winner_id == invited
    assert revision.previous_result.winner_id == creator
    assert revision.new_result.winner_id == invited
    assert revision.reason == "score fixed"

    with pytest.raises(CorrectionWindowExpiredError):
        await service.correct_result(
            FakeUow(repository),
            actor_id=creator,
            match_id=corrected.match.id,
            scores=[SetScore(11, 1), SetScore(11, 2), SetScore(11, 3)],
            expected_version=corrected.match.version,
            reason=None,
            now=completed.match.completed_at + timedelta(hours=24, microseconds=1),
        )


async def test_standalone_commands_reject_session_matches(setup) -> None:
    service, repository, creator, invited, _ = setup
    active = await active_match(service, repository, creator, invited)
    repository.views[active.match.id] = replace(
        active,
        match=replace(
            active.match,
            context=MatchContext.GAME_SESSION,
            game_session_id=uuid4(),
            sequence_number=1,
        ),
    )

    with pytest.raises(MatchStateError, match="game session workflow"):
        await service.add_set(
            FakeUow(repository),
            actor_id=creator,
            match_id=active.match.id,
            score=SetScore(11, 3),
            expected_version=active.match.version,
        )
    with pytest.raises(MatchStateError, match="game session workflow"):
        await service.record_full_result(
            FakeUow(repository),
            actor_id=creator,
            match_id=active.match.id,
            scores=[SetScore(11, 3), SetScore(11, 4), SetScore(11, 5)],
        )
    with pytest.raises(MatchStateError, match="game session workflow"):
        await service.cancel(FakeUow(repository), actor_id=creator, match_id=active.match.id)
