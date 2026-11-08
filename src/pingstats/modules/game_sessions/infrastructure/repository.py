"""PostgreSQL repositories for game session lifecycle and membership."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.game_sessions.application.dto import (
    GameSessionParticipantItem,
    GameSessionView,
)
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueEvent,
    GameSessionQueueState,
    GameSessionStatus,
)
from pingstats.modules.game_sessions.infrastructure.models import (
    GameSessionCurrentPairModel,
    GameSessionModel,
    GameSessionParticipantModel,
    GameSessionProcessedResultModel,
    GameSessionQueueEventModel,
    GameSessionQueueModel,
)
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.matches.infrastructure.models import MatchModel, MatchParticipantModel
from pingstats.modules.users.domain import User
from pingstats.modules.users.infrastructure.models import UserModel


class SqlAlchemyGameSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user(self, user_id: UUID) -> bool:
        statement = select(UserModel.id).where(UserModel.id == user_id).with_for_update()
        return await self._session.scalar(statement) is not None

    async def user_exists(self, user_id: UUID) -> bool:
        return bool(await self._session.scalar(select(exists().where(UserModel.id == user_id))))

    async def create_session(
        self,
        *,
        organizer_id: UUID,
        name: str | None,
        points_to_win: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> GameSession:
        model = GameSessionModel(
            id=uuid4(),
            organizer_id=organizer_id,
            status=GameSessionStatus.PENDING,
            name=name,
            points_to_win=points_to_win,
            sets_to_win=sets_to_win,
            is_ranked=is_ranked,
            version=1,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> GameSession | None:
        statement = select(GameSessionModel).where(GameSessionModel.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_view(self, session_id: UUID) -> GameSessionView | None:
        session = await self.get_session(session_id)
        if session is None:
            return None
        participants, _ = await self.list_participants(
            session_id,
            active_only=True,
            limit=10_000,
            offset=0,
        )
        return GameSessionView(session=session, active_participants=tuple(participants))

    async def list_for_user(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[GameSessionView], int]:
        participated = exists().where(
            GameSessionParticipantModel.session_id == GameSessionModel.id,
            GameSessionParticipantModel.user_id == user_id,
        )
        predicate = participated
        total = await self._session.scalar(
            select(func.count()).select_from(GameSessionModel).where(predicate)
        )
        ids = (
            await self._session.scalars(
                select(GameSessionModel.id)
                .where(predicate)
                .order_by(GameSessionModel.created_at.desc(), GameSessionModel.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        views = []
        for session_id in ids:
            view = await self.get_view(session_id)
            if view is not None:
                views.append(view)
        return tuple(views), int(total or 0)

    async def has_participated(self, session_id: UUID, user_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        GameSessionParticipantModel.session_id == session_id,
                        GameSessionParticipantModel.user_id == user_id,
                    )
                )
            )
        )

    async def get_active_participant(
        self, session_id: UUID, user_id: UUID
    ) -> GameSessionParticipant | None:
        model = await self._session.scalar(
            select(GameSessionParticipantModel).where(
                GameSessionParticipantModel.session_id == session_id,
                GameSessionParticipantModel.user_id == user_id,
                GameSessionParticipantModel.left_at.is_(None),
            )
        )
        return model.to_domain() if model is not None else None

    async def add_participant(
        self, session_id: UUID, user_id: UUID, *, joined_at: datetime
    ) -> GameSessionParticipant:
        model = GameSessionParticipantModel(
            id=uuid4(),
            session_id=session_id,
            user_id=user_id,
            joined_at=joined_at,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def close_participant(
        self, participant: GameSessionParticipant, *, left_at: datetime
    ) -> GameSessionParticipant:
        statement = (
            update(GameSessionParticipantModel)
            .where(
                GameSessionParticipantModel.id == participant.id,
                GameSessionParticipantModel.left_at.is_(None),
            )
            .values(left_at=left_at)
            .returning(GameSessionParticipantModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        if model is None:
            raise RuntimeError("Active participation changed while session was locked")
        return model.to_domain()

    async def count_active_participants(self, session_id: UUID) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(GameSessionParticipantModel)
            .where(
                GameSessionParticipantModel.session_id == session_id,
                GameSessionParticipantModel.left_at.is_(None),
            )
        )
        return int(count or 0)

    async def list_active_participants(self, session_id: UUID) -> Sequence[GameSessionParticipant]:
        models = (
            await self._session.scalars(
                select(GameSessionParticipantModel)
                .where(
                    GameSessionParticipantModel.session_id == session_id,
                    GameSessionParticipantModel.left_at.is_(None),
                )
                .order_by(
                    GameSessionParticipantModel.joined_at,
                    GameSessionParticipantModel.id,
                )
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def get_participants_by_ids(
        self, session_id: UUID, participant_ids: Sequence[UUID]
    ) -> Sequence[GameSessionParticipant]:
        if not participant_ids:
            return ()
        models = (
            await self._session.scalars(
                select(GameSessionParticipantModel).where(
                    GameSessionParticipantModel.session_id == session_id,
                    GameSessionParticipantModel.id.in_(participant_ids),
                )
            )
        ).all()
        by_id = {model.id: model.to_domain() for model in models}
        return tuple(
            by_id[participant_id] for participant_id in participant_ids if participant_id in by_id
        )

    async def get_queue_state(self, session_id: UUID) -> GameSessionQueueState | None:
        pair = await self._session.get(GameSessionCurrentPairModel, session_id)
        if pair is None:
            return None
        waiting = (
            await self._session.scalars(
                select(GameSessionQueueModel.participant_id)
                .where(GameSessionQueueModel.session_id == session_id)
                .order_by(GameSessionQueueModel.position)
            )
        ).all()
        return GameSessionQueueState(
            current_pair=(pair.player1_participant_id, pair.player2_participant_id),
            waiting=tuple(waiting),
        )

    async def save_queue_state(self, session_id: UUID, state: GameSessionQueueState) -> None:
        state.validate()
        pair = await self._session.get(GameSessionCurrentPairModel, session_id)
        if pair is None:
            self._session.add(
                GameSessionCurrentPairModel(
                    session_id=session_id,
                    player1_participant_id=state.current_pair[0],
                    player2_participant_id=state.current_pair[1],
                )
            )
        else:
            pair.player1_participant_id = state.current_pair[0]
            pair.player2_participant_id = state.current_pair[1]

        await self._session.execute(
            text("SET CONSTRAINTS uq_game_session_queue_session_position DEFERRED")
        )
        await self._session.execute(
            text("SET CONSTRAINTS uq_game_session_queue_session_participant DEFERRED")
        )
        await self._session.execute(
            delete(GameSessionQueueModel).where(GameSessionQueueModel.session_id == session_id)
        )
        self._session.add_all(
            [
                GameSessionQueueModel(
                    id=uuid4(),
                    session_id=session_id,
                    participant_id=participant_id,
                    position=position,
                )
                for position, participant_id in enumerate(state.waiting, start=1)
            ]
        )
        await self._session.flush()

    async def clear_queue_state(self, session_id: UUID) -> None:
        await self._session.execute(
            delete(GameSessionQueueModel).where(GameSessionQueueModel.session_id == session_id)
        )
        await self._session.execute(
            delete(GameSessionCurrentPairModel).where(
                GameSessionCurrentPairModel.session_id == session_id
            )
        )
        await self._session.flush()

    async def has_processed_result(self, session_id: UUID, result_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    exists().where(
                        GameSessionProcessedResultModel.session_id == session_id,
                        GameSessionProcessedResultModel.result_id == result_id,
                    )
                )
            )
        )

    async def mark_result_processed(self, session_id: UUID, result_id: UUID) -> None:
        self._session.add(
            GameSessionProcessedResultModel(
                id=uuid4(),
                session_id=session_id,
                result_id=result_id,
            )
        )
        await self._session.flush()

    async def update_session(
        self,
        session: GameSession,
        *,
        status: GameSessionStatus | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> GameSession:
        values: dict[str, object] = {"version": session.version + 1}
        if status is not None:
            values["status"] = status
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        statement = (
            update(GameSessionModel)
            .where(GameSessionModel.id == session.id, GameSessionModel.version == session.version)
            .values(**values)
            .returning(GameSessionModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        if model is None:
            raise RuntimeError("Optimistic session update failed while row was locked")
        return model.to_domain()

    async def add_event(
        self,
        *,
        session_id: UUID,
        participant_id: UUID | None,
        event_type: GameSessionEventType,
        payload: dict[str, object],
    ) -> GameSessionQueueEvent:
        model = GameSessionQueueEventModel(
            id=uuid4(),
            session_id=session_id,
            participant_id=participant_id,
            event_type=event_type.value,
            payload=payload,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def list_participants(
        self,
        session_id: UUID,
        *,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[GameSessionParticipantItem], int]:
        filters = [GameSessionParticipantModel.session_id == session_id]
        if active_only:
            filters.append(GameSessionParticipantModel.left_at.is_(None))
        total = await self._session.scalar(
            select(func.count()).select_from(GameSessionParticipantModel).where(*filters)
        )
        rows = (
            await self._session.execute(
                select(GameSessionParticipantModel, UserModel)
                .join(UserModel, UserModel.id == GameSessionParticipantModel.user_id)
                .where(*filters)
                .order_by(
                    GameSessionParticipantModel.joined_at,
                    GameSessionParticipantModel.id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return (
            tuple(
                GameSessionParticipantItem(participation=model.to_domain(), user=user.to_domain())
                for model, user in rows
            ),
            int(total or 0),
        )

    async def list_historical_users(self, session_id: UUID) -> Sequence[User]:
        models = (
            await self._session.scalars(
                select(UserModel)
                .join(
                    GameSessionParticipantModel,
                    GameSessionParticipantModel.user_id == UserModel.id,
                )
                .where(GameSessionParticipantModel.session_id == session_id)
                .distinct()
                .order_by(UserModel.id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)


class SqlAlchemyCurrentSessionMatchChecker:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_active_match(self, session_id: UUID, user_id: UUID | None = None) -> bool:
        statement = select(
            exists().where(
                MatchModel.game_session_id == session_id,
                MatchModel.status == MatchStatus.ACTIVE,
            )
        )
        if user_id is not None:
            statement = select(
                exists()
                .where(
                    MatchModel.game_session_id == session_id,
                    MatchModel.status == MatchStatus.ACTIVE,
                )
                .where(
                    exists().where(
                        MatchParticipantModel.match_id == MatchModel.id,
                        MatchParticipantModel.user_id == user_id,
                    )
                )
            )
        return bool(await self._session.scalar(statement))
