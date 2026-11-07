"""PostgreSQL match repository."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchResultRevision,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)
from pingstats.modules.matches.infrastructure.models import (
    MatchModel,
    MatchParticipantModel,
    MatchResultRevisionModel,
    MatchSetModel,
)
from pingstats.modules.users.infrastructure.models import UserModel


class SqlAlchemyMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]:
        ids = sorted((first_id, second_id))
        statement = (
            select(UserModel.id)
            .where(UserModel.id.in_(ids))
            .order_by(UserModel.id)
            .with_for_update()
        )
        return set((await self._session.scalars(statement)).all())

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
        match = MatchModel(
            id=uuid4(),
            context=MatchContext.STANDALONE,
            status=MatchStatus.PENDING,
            created_by=created_by,
            points_to_win=points_to_win,
            win_by=win_by,
            sets_to_win=sets_to_win,
            is_ranked=is_ranked,
            version=1,
        )
        self._session.add(match)
        await self._session.flush()
        self._session.add_all(
            [
                MatchParticipantModel(
                    id=uuid4(), match_id=match.id, user_id=created_by, position=1
                ),
                MatchParticipantModel(
                    id=uuid4(), match_id=match.id, user_id=invited_user_id, position=2
                ),
            ]
        )
        await self._session.flush()
        view = await self.get_view(match.id)
        if view is None:
            raise RuntimeError("Created match could not be loaded")
        return view

    async def create_game_session_match(
        self,
        *,
        created_by: UUID,
        session_id: UUID,
        sequence_number: int,
        player1_user_id: UUID,
        player2_user_id: UUID,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        is_ranked: bool,
        started_at: datetime,
    ) -> MatchView:
        match = MatchModel(
            id=uuid4(),
            context=MatchContext.GAME_SESSION,
            status=MatchStatus.ACTIVE,
            created_by=created_by,
            points_to_win=points_to_win,
            win_by=win_by,
            sets_to_win=sets_to_win,
            is_ranked=is_ranked,
            game_session_id=session_id,
            sequence_number=sequence_number,
            started_at=started_at,
            version=1,
        )
        self._session.add(match)
        await self._session.flush()
        self._session.add_all(
            [
                MatchParticipantModel(
                    id=uuid4(), match_id=match.id, user_id=player1_user_id, position=1
                ),
                MatchParticipantModel(
                    id=uuid4(), match_id=match.id, user_id=player2_user_id, position=2
                ),
            ]
        )
        await self._session.flush()
        view = await self.get_view(match.id)
        if view is None:
            raise RuntimeError("Created session match could not be loaded")
        return view

    async def get_current_for_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> MatchView | None:
        statement = select(MatchModel.id).where(
            MatchModel.game_session_id == session_id,
            MatchModel.context == MatchContext.GAME_SESSION,
            MatchModel.status == MatchStatus.ACTIVE,
        )
        if for_update:
            statement = statement.with_for_update()
        match_id = await self._session.scalar(statement)
        if match_id is None:
            return None
        return await self.get_view(match_id)

    async def next_session_sequence(self, session_id: UUID) -> int:
        current = await self._session.scalar(
            select(func.max(MatchModel.sequence_number)).where(
                MatchModel.game_session_id == session_id,
                MatchModel.context == MatchContext.GAME_SESSION,
            )
        )
        return int(current or 0) + 1

    async def get_view(self, match_id: UUID, *, for_update: bool = False) -> MatchView | None:
        match_statement = select(MatchModel).where(MatchModel.id == match_id)
        if for_update:
            match_statement = match_statement.with_for_update()
        match = await self._session.scalar(match_statement)
        if match is None:
            return None
        participant_statement = (
            select(MatchParticipantModel, UserModel)
            .join(UserModel, UserModel.id == MatchParticipantModel.user_id)
            .where(MatchParticipantModel.match_id == match_id)
            .order_by(MatchParticipantModel.position)
        )
        participant_rows = (await self._session.execute(participant_statement)).all()
        set_statement = (
            select(MatchSetModel)
            .where(MatchSetModel.match_id == match_id)
            .order_by(MatchSetModel.set_number)
        )
        sets = (await self._session.scalars(set_statement)).all()
        return MatchView(
            match=match.to_domain(),
            participants=tuple(
                (participant.to_domain(), user.to_domain())
                for participant, user in participant_rows
            ),
            sets=tuple(item.to_domain() for item in sets),
        )

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: MatchStatus | None,
        context: MatchContext | None,
        is_ranked: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MatchView], int]:
        filters = [MatchParticipantModel.user_id == user_id]
        if status is not None:
            filters.append(MatchModel.status == status)
        if context is not None:
            filters.append(MatchModel.context == context)
        if is_ranked is not None:
            filters.append(MatchModel.is_ranked == is_ranked)
        base = (
            select(MatchModel.id)
            .join(MatchParticipantModel, MatchParticipantModel.match_id == MatchModel.id)
            .where(*filters)
        )
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        ids = (
            await self._session.scalars(
                base.order_by(MatchModel.created_at.desc(), MatchModel.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        views = []
        for match_id in ids:
            view = await self.get_view(match_id)
            if view is not None:
                views.append(view)
        return tuple(views), int(total or 0)

    async def list_for_session(
        self, session_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchView], int]:
        predicate = (MatchModel.game_session_id == session_id) & (
            MatchModel.context == MatchContext.GAME_SESSION
        )
        total = await self._session.scalar(
            select(func.count()).select_from(MatchModel).where(predicate)
        )
        ids = (
            await self._session.scalars(
                select(MatchModel.id)
                .where(predicate)
                .order_by(MatchModel.sequence_number, MatchModel.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        views = []
        for match_id in ids:
            view = await self.get_view(match_id)
            if view is not None:
                views.append(view)
        return tuple(views), int(total or 0)

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
        values: dict[str, object] = {
            "updated_at": func.now(),
            "version": match.version + 1,
        }
        if status is not None:
            values["status"] = status
        if winner_id is not None:
            values["winner_id"] = winner_id
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if cancelled_at is not None:
            values["cancelled_at"] = cancelled_at
        statement = (
            update(MatchModel)
            .where(MatchModel.id == match.id, MatchModel.version == match.version)
            .values(**values)
            .returning(MatchModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        if model is None:
            raise RuntimeError("Optimistic match update failed while row was locked")
        return model.to_domain()

    async def add_set(self, match_id: UUID, set_number: int, score: SetScore) -> None:
        self._session.add(
            MatchSetModel(
                id=uuid4(),
                match_id=match_id,
                set_number=set_number,
                player1_score=score.player1_score,
                player2_score=score.player2_score,
            )
        )
        await self._session.flush()

    async def replace_sets(self, match_id: UUID, scores: Sequence[SetScore]) -> None:
        await self._session.execute(delete(MatchSetModel).where(MatchSetModel.match_id == match_id))
        self._session.add_all(
            [
                MatchSetModel(
                    id=uuid4(),
                    match_id=match_id,
                    set_number=number,
                    player1_score=score.player1_score,
                    player2_score=score.player2_score,
                )
                for number, score in enumerate(scores, start=1)
            ]
        )
        await self._session.flush()

    async def create_revision(
        self,
        *,
        match_id: UUID,
        changed_by: UUID,
        previous_result: ResultSnapshot,
        new_result: ResultSnapshot,
        reason: str | None,
    ) -> MatchResultRevision:
        current = await self._session.scalar(
            select(func.max(MatchResultRevisionModel.revision_number)).where(
                MatchResultRevisionModel.match_id == match_id
            )
        )
        model = MatchResultRevisionModel(
            id=uuid4(),
            match_id=match_id,
            revision_number=int(current or 0) + 1,
            changed_by=changed_by,
            previous_result=previous_result.to_json(),
            new_result=new_result.to_json(),
            reason=reason,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def list_revisions(
        self, match_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchResultRevision], int]:
        predicate = MatchResultRevisionModel.match_id == match_id
        total = await self._session.scalar(
            select(func.count()).select_from(MatchResultRevisionModel).where(predicate)
        )
        statement = (
            select(MatchResultRevisionModel)
            .where(predicate)
            .order_by(MatchResultRevisionModel.revision_number.desc())
            .offset(offset)
            .limit(limit)
        )
        models = (await self._session.scalars(statement)).all()
        return tuple(model.to_domain() for model in models), int(total or 0)
