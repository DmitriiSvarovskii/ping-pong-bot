"""Application tests for the global leaderboard query."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pingstats.modules.leaderboard.application import (
    LeaderboardService,
    RankedRating,
)
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.users.domain import User
from tests.unit.test_rating_queries import make_period, make_rating


def make_user(user_id: UUID) -> User:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    return User(
        id=user_id,
        telegram_id=None,
        first_name="Rated",
        last_name="Player",
        username="rated_player",
        photo_url="https://example.test/player.jpg",
        created_at=now,
        updated_at=now,
        last_active_at=now,
    )


class LeaderboardRepository:
    def __init__(self, rows: tuple[RankedRating, ...] = ()) -> None:
        self.rows = rows
        self.last_page: tuple[int, int] | None = None

    async def list_ranked(
        self,
        *,
        current_user_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RankedRating, ...], int, int | None]:
        self.last_page = (limit, offset)
        current_position = next(
            (row.position for row in self.rows if row.user.id == current_user_id), None
        )
        return self.rows[offset : offset + limit], len(self.rows), current_position


async def test_leaderboard_projects_effective_rd_and_current_position() -> None:
    first_id, current_id = uuid4(), uuid4()
    period = make_period()
    rows = (
        RankedRating(1, make_user(first_id), make_rating(first_id, period), period),
        RankedRating(2, make_user(current_id), make_rating(current_id, period), period),
    )
    repository = LeaderboardRepository(rows)
    service = LeaderboardService(
        RatingQueryService(clock=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC))
    )

    page = await service.get_page(
        repository,
        current_user_id=current_id,
        limit=1,
        offset=0,
    )

    assert page.total == 2
    assert page.current_user_position == 2
    assert [item.position for item in page.items] == [1]
    assert page.items[0].effective_rd > page.items[0].rd
    assert page.items[0].photo_url == "https://example.test/player.jpg"
    assert repository.last_page == (1, 0)


async def test_empty_leaderboard_does_not_create_current_position() -> None:
    page = await LeaderboardService(RatingQueryService()).get_page(
        LeaderboardRepository(),
        current_user_id=uuid4(),
        limit=20,
        offset=0,
    )

    assert page.items == ()
    assert page.total == 0
    assert page.current_user_position is None
