"""Small in-memory rating repositories shared by application unit tests."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
)


class FakeRatingRepository:
    def __init__(self) -> None:
        self.by_user: dict[UUID, PublishedRating] = {}

    async def add(self, rating: PublishedRating) -> PublishedRating:
        self.by_user[rating.user_id] = rating
        return rating

    async def get(self, user_id: UUID, *, for_update: bool = False) -> PublishedRating | None:
        del for_update
        return self.by_user.get(user_id)

    async def list_all(self, *, for_update: bool = False) -> tuple[PublishedRating, ...]:
        del for_update
        return tuple(sorted(self.by_user.values(), key=lambda item: item.user_id.int))

    async def delete(self, user_id: UUID) -> None:
        self.by_user.pop(user_id, None)

    async def update_published_state(
        self,
        *,
        user_id: UUID,
        rating: Decimal,
        rd: Decimal,
        volatility: Decimal,
        as_of_period_id: UUID,
        rated_matches_count: int,
        expected_version: int,
    ) -> PublishedRating | None:
        current = self.by_user.get(user_id)
        if current is None or current.version != expected_version:
            return None
        updated = replace(
            current,
            rating=rating,
            rd=rd,
            volatility=volatility,
            as_of_period_id=as_of_period_id,
            rated_matches_count=rated_matches_count,
            updated_at=datetime.now(UTC),
            version=expected_version + 1,
        )
        self.by_user[user_id] = updated
        return updated


class FakeRatingPeriodRepository:
    def __init__(self) -> None:
        self.by_id: dict[UUID, RatingPeriod] = {}
        self.by_start: dict[datetime, RatingPeriod] = {}
        self.locked_starts: list[datetime] = []
        self.publication_locks = 0

    async def acquire_publication_lock(self) -> None:
        self.publication_locks += 1

    async def acquire_period_lock(self, starts_at: datetime) -> None:
        self.locked_starts.append(starts_at)

    async def add(self, period: RatingPeriod) -> RatingPeriod:
        existing = self.by_start.get(period.starts_at)
        if existing is not None:
            return existing
        self.by_id[period.id] = period
        self.by_start[period.starts_at] = period
        return period

    async def get_by_start(
        self, starts_at: datetime, *, for_update: bool = False
    ) -> RatingPeriod | None:
        del for_update
        return self.by_start.get(starts_at)

    async def get(self, period_id: UUID, *, for_update: bool = False) -> RatingPeriod | None:
        del for_update
        return self.by_id.get(period_id)

    async def get_previous(self, starts_at: datetime) -> RatingPeriod | None:
        earlier = [period for period in self.by_id.values() if period.starts_at < starts_at]
        return max(earlier, key=lambda period: (period.starts_at, period.id), default=None)

    async def get_previous_published(self, starts_at: datetime) -> RatingPeriod | None:
        earlier = [
            period
            for period in self.by_id.values()
            if period.starts_at < starts_at and period.status is RatingPeriodStatus.PUBLISHED
        ]
        return max(earlier, key=lambda period: (period.starts_at, period.id), default=None)

    async def get_next_published(self, starts_at: datetime) -> RatingPeriod | None:
        later = [
            period
            for period in self.by_id.values()
            if period.starts_at > starts_at and period.status is RatingPeriodStatus.PUBLISHED
        ]
        return min(later, key=lambda period: (period.starts_at, period.id), default=None)

    async def get_active_published_version(self, period_id: UUID) -> int | None:
        period = self.by_id.get(period_id)
        if period is None or period.status is not RatingPeriodStatus.PUBLISHED:
            return None
        return period.published_version

    async def list_open_ending_at_or_before(self, ends_at: datetime) -> tuple[RatingPeriod, ...]:
        return tuple(
            sorted(
                (
                    period
                    for period in self.by_id.values()
                    if period.status is RatingPeriodStatus.OPEN and period.ends_at <= ends_at
                ),
                key=lambda period: (period.starts_at, period.id),
            )
        )

    async def list_calculating(self) -> tuple[RatingPeriod, ...]:
        return tuple(
            sorted(
                (
                    period
                    for period in self.by_id.values()
                    if period.status is RatingPeriodStatus.CALCULATING
                ),
                key=lambda period: (period.starts_at, period.id),
            )
        )

    async def list_non_open_from(self, starts_at: datetime) -> tuple[RatingPeriod, ...]:
        return tuple(
            sorted(
                (
                    period
                    for period in self.by_id.values()
                    if period.starts_at >= starts_at
                    and period.status is not RatingPeriodStatus.OPEN
                ),
                key=lambda period: (period.starts_at, period.id),
            )
        )

    async def list_non_open_between(
        self, starts_at: datetime, ends_at: datetime
    ) -> tuple[RatingPeriod, ...]:
        return tuple(
            sorted(
                (
                    period
                    for period in self.by_id.values()
                    if starts_at <= period.starts_at <= ends_at
                    and period.status is not RatingPeriodStatus.OPEN
                ),
                key=lambda period: (period.starts_at, period.id),
            )
        )

    async def reserve_calculation_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if (
            period is None
            or period.version != expected_version
            or period.calculation_version >= calculation_version
        ):
            return None
        updated = replace(
            period,
            calculation_version=calculation_version,
            calculated_version=None,
            updated_at=datetime.now(UTC),
            version=expected_version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated

    async def update_state(
        self,
        *,
        period_id: UUID,
        status: RatingPeriodStatus,
        calculation_version: int,
        published_at: datetime | None,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if period is None or period.version != expected_version:
            return None
        updated = replace(
            period,
            status=status,
            calculation_version=calculation_version,
            published_at=published_at,
            version=period.version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated

    async def mark_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if (
            period is None
            or period.status is not RatingPeriodStatus.CALCULATING
            or period.calculation_version != calculation_version
            or period.version != expected_version
        ):
            return None
        updated = replace(
            period,
            calculated_version=calculation_version,
            version=period.version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated

    async def mark_shadow_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if (
            period is None
            or period.status is RatingPeriodStatus.OPEN
            or period.calculation_version != calculation_version
            or period.version != expected_version
        ):
            return None
        updated = replace(
            period,
            calculated_version=calculation_version,
            version=period.version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated

    async def mark_published(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if (
            period is None
            or period.status is not RatingPeriodStatus.CALCULATING
            or period.calculation_version != calculation_version
            or period.calculated_version != calculation_version
            or period.version != expected_version
        ):
            return None
        updated = replace(
            period,
            status=RatingPeriodStatus.PUBLISHED,
            published_version=calculation_version,
            published_at=published_at,
            version=period.version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated

    async def activate_rebuild_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None:
        period = self.by_id.get(period_id)
        if (
            period is None
            or period.status
            not in {
                RatingPeriodStatus.CALCULATING,
                RatingPeriodStatus.PUBLISHED,
            }
            or period.calculation_version != calculation_version
            or period.calculated_version != calculation_version
            or period.version != expected_version
        ):
            return None
        updated = replace(
            period,
            status=RatingPeriodStatus.PUBLISHED,
            published_version=calculation_version,
            published_at=period.published_at or published_at,
            version=period.version + 1,
        )
        self.by_id[period_id] = updated
        self.by_start[period.starts_at] = updated
        return updated


class FakeRatingInputRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, int, UUID], RatingPeriodMatchInput] = {}

    async def add_if_absent(
        self, item: RatingPeriodMatchInput
    ) -> tuple[RatingPeriodMatchInput, bool]:
        key = (item.period_id, item.calculation_version, item.match_id)
        existing = self.items.get(key)
        if existing is not None:
            return existing, False
        self.items[key] = item
        return item, True

    async def add_all(
        self, items: tuple[RatingPeriodMatchInput, ...]
    ) -> tuple[RatingPeriodMatchInput, ...]:
        for item in items:
            self.items[(item.period_id, item.calculation_version, item.match_id)] = item
        return items

    async def get(
        self, period_id: UUID, calculation_version: int, match_id: UUID
    ) -> RatingPeriodMatchInput | None:
        return self.items.get((period_id, calculation_version, match_id))

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> tuple[RatingPeriodMatchInput, ...]:
        return tuple(
            item for key, item in self.items.items() if key[:2] == (period_id, calculation_version)
        )


class FakeRatingResultRepository:
    def __init__(self) -> None:
        self.items: list[RatingPeriodResult] = []

    async def add_all(
        self, results: tuple[RatingPeriodResult, ...]
    ) -> tuple[RatingPeriodResult, ...]:
        self.items.extend(results)
        return results

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> tuple[RatingPeriodResult, ...]:
        return tuple(
            item
            for item in self.items
            if item.period_id == period_id and item.calculation_version == calculation_version
        )
