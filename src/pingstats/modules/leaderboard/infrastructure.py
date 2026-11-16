"""PostgreSQL leaderboard read model."""

from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.leaderboard.application import RankedRating
from pingstats.modules.ratings.infrastructure.models import RatingModel, RatingPeriodModel
from pingstats.modules.ratings.infrastructure.ranking import published_rating_ranking
from pingstats.modules.ratings.infrastructure.repository import (
    acquire_rating_publication_read_lock,
)
from pingstats.modules.users.infrastructure.models import UserModel


class SqlAlchemyLeaderboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_ranked(
        self,
        *,
        current_user_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RankedRating, ...], int, int | None]:
        await acquire_rating_publication_read_lock(self._session)
        ranked = published_rating_ranking(name="ranked_leaderboard")
        total, current_position = (
            await self._session.execute(
                select(
                    func.count(),
                    func.max(
                        case(
                            (ranked.c.user_id == current_user_id, ranked.c.position),
                            else_=None,
                        )
                    ),
                ).select_from(ranked)
            )
        ).one()
        rows = (
            await self._session.execute(
                select(ranked.c.position, UserModel, RatingModel, RatingPeriodModel)
                .join(UserModel, UserModel.id == ranked.c.user_id)
                .join(RatingModel, RatingModel.user_id == ranked.c.user_id)
                .join(RatingPeriodModel, RatingPeriodModel.id == RatingModel.as_of_period_id)
                .order_by(ranked.c.position)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return (
            tuple(
                RankedRating(
                    position=int(position),
                    user=user.to_domain(),
                    rating=rating.to_domain(),
                    period=period.to_domain(),
                )
                for position, user, rating, period in rows
            ),
            int(total),
            int(current_position) if current_position is not None else None,
        )
