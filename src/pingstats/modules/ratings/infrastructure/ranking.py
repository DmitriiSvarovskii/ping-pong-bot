"""Shared PostgreSQL ranking expression for published ratings."""

from sqlalchemy import func, select
from sqlalchemy.sql.selectable import CTE

from pingstats.modules.ratings.domain import RatingPeriodStatus
from pingstats.modules.ratings.infrastructure.models import RatingModel, RatingPeriodModel


def published_rating_ranking(*, name: str) -> CTE:
    """Rank every active published rating using the canonical leaderboard order."""
    return (
        select(
            RatingModel.user_id.label("user_id"),
            func.row_number()
            .over(
                order_by=(
                    RatingModel.rating.desc(),
                    RatingModel.rd.asc(),
                    RatingModel.rated_matches_count.desc(),
                    RatingModel.user_id.asc(),
                )
            )
            .label("position"),
        )
        .join(RatingPeriodModel, RatingPeriodModel.id == RatingModel.as_of_period_id)
        .where(
            RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
            RatingPeriodModel.published_version.is_not(None),
        )
        .cte(name)
    )
