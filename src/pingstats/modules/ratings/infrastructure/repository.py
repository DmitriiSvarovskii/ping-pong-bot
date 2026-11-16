"""PostgreSQL repositories for versioned rating persistence."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationRequest,
    RatingRecalculationStatus,
)
from pingstats.modules.ratings.infrastructure.models import (
    RatingModel,
    RatingPeriodMatchInputModel,
    RatingPeriodModel,
    RatingPeriodResultModel,
    RatingRebuildJobModel,
    RatingRebuildPeriodModel,
    RatingRecalculationRequestModel,
)

_RATING_PERIOD_LOCK_NAMESPACE = 1_347_632_210
_RATING_PUBLICATION_LOCK_NAMESPACE = 1_347_632_211
_RATING_PUBLICATION_LOCK_KEY = 1
_ACTIVE_REBUILD_STATUSES = (
    RatingRebuildStatus.PENDING,
    RatingRebuildStatus.PREPARING,
    RatingRebuildStatus.READY,
    RatingRebuildStatus.ACTIVATING,
    RatingRebuildStatus.FAILED,
)


async def acquire_rating_publication_read_lock(session: AsyncSession) -> None:
    """Serialize published read models with publisher and rebuild cutover."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock_shared(
                _RATING_PUBLICATION_LOCK_NAMESPACE,
                _RATING_PUBLICATION_LOCK_KEY,
            )
        )
    )


class SqlAlchemyRatingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, rating: PublishedRating) -> PublishedRating:
        model = RatingModel(
            user_id=rating.user_id,
            rating=rating.rating,
            rd=rating.rd,
            volatility=rating.volatility,
            as_of_period_id=rating.as_of_period_id,
            rated_matches_count=rating.rated_matches_count,
            updated_at=rating.updated_at,
            version=rating.version,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get(self, user_id: UUID, *, for_update: bool = False) -> PublishedRating | None:
        statement = select(RatingModel).where(RatingModel.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def list_all(self, *, for_update: bool = False) -> Sequence[PublishedRating]:
        statement = select(RatingModel).order_by(RatingModel.user_id)
        if for_update:
            statement = statement.with_for_update()
        models = (await self._session.scalars(statement)).all()
        return tuple(model.to_domain() for model in models)

    async def delete(self, user_id: UUID) -> None:
        await self._session.execute(delete(RatingModel).where(RatingModel.user_id == user_id))

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
        statement = (
            update(RatingModel)
            .where(RatingModel.user_id == user_id, RatingModel.version == expected_version)
            .values(
                rating=rating,
                rd=rd,
                volatility=volatility,
                as_of_period_id=as_of_period_id,
                rated_matches_count=rated_matches_count,
                updated_at=func.now(),
                version=expected_version + 1,
            )
            .returning(RatingModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyRatingQueryRepository:
    """Read the active branch under a shared publication advisory lock."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _acquire_publication_read_lock(self) -> None:
        await acquire_rating_publication_read_lock(self._session)

    async def get_current(self, user_id: UUID) -> tuple[PublishedRating, RatingPeriod] | None:
        await self._acquire_publication_read_lock()
        row = (
            await self._session.execute(
                select(RatingModel, RatingPeriodModel)
                .join(RatingPeriodModel, RatingPeriodModel.id == RatingModel.as_of_period_id)
                .where(
                    RatingModel.user_id == user_id,
                    RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
                    RatingPeriodModel.published_version.is_not(None),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        rating, period = row
        return rating.to_domain(), period.to_domain()

    async def list_history(
        self,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[tuple[RatingPeriodResult, RatingPeriod]], int]:
        await self._acquire_publication_read_lock()
        join_condition = (RatingPeriodResultModel.period_id == RatingPeriodModel.id) & (
            RatingPeriodResultModel.calculation_version == RatingPeriodModel.published_version
        )
        filters = (
            RatingPeriodResultModel.user_id == user_id,
            RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
            RatingPeriodModel.published_version.is_not(None),
            RatingPeriodModel.published_at.is_not(None),
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(RatingPeriodResultModel)
            .join(RatingPeriodModel, join_condition)
            .where(*filters)
        )
        rows = (
            await self._session.execute(
                select(RatingPeriodResultModel, RatingPeriodModel)
                .join(RatingPeriodModel, join_condition)
                .where(*filters)
                .order_by(RatingPeriodModel.starts_at.desc(), RatingPeriodModel.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return (
            tuple((result.to_domain(), period.to_domain()) for result, period in rows),
            int(total or 0),
        )


class SqlAlchemyRatingPeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_publication_lock(self) -> None:
        await self._session.execute(
            select(
                func.pg_advisory_xact_lock(
                    _RATING_PUBLICATION_LOCK_NAMESPACE,
                    _RATING_PUBLICATION_LOCK_KEY,
                )
            )
        )

    async def acquire_period_lock(self, starts_at: datetime) -> None:
        lock_key = starts_at.astimezone(UTC).date().toordinal()
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_RATING_PERIOD_LOCK_NAMESPACE, lock_key))
        )

    async def add(self, period: RatingPeriod) -> RatingPeriod:
        model = RatingPeriodModel(
            id=period.id,
            starts_at=period.starts_at,
            ends_at=period.ends_at,
            status=period.status,
            algorithm=period.algorithm,
            algorithm_version=period.algorithm_version,
            parameters=period.parameters,
            calculation_version=period.calculation_version,
            calculated_version=period.calculated_version,
            published_version=period.published_version,
            published_at=period.published_at,
            created_at=period.created_at,
            updated_at=period.updated_at,
            version=period.version,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get(self, period_id: UUID, *, for_update: bool = False) -> RatingPeriod | None:
        statement = select(RatingPeriodModel).where(RatingPeriodModel.id == period_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_by_start(
        self, starts_at: datetime, *, for_update: bool = False
    ) -> RatingPeriod | None:
        statement = select(RatingPeriodModel).where(RatingPeriodModel.starts_at == starts_at)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_previous(self, starts_at: datetime) -> RatingPeriod | None:
        model = await self._session.scalar(
            select(RatingPeriodModel)
            .where(RatingPeriodModel.starts_at < starts_at)
            .order_by(RatingPeriodModel.starts_at.desc(), RatingPeriodModel.id.desc())
            .limit(1)
        )
        return model.to_domain() if model is not None else None

    async def get_previous_published(self, starts_at: datetime) -> RatingPeriod | None:
        model = await self._session.scalar(
            select(RatingPeriodModel)
            .where(
                RatingPeriodModel.starts_at < starts_at,
                RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
            )
            .order_by(RatingPeriodModel.starts_at.desc(), RatingPeriodModel.id.desc())
            .limit(1)
        )
        return model.to_domain() if model is not None else None

    async def get_next_published(self, starts_at: datetime) -> RatingPeriod | None:
        model = await self._session.scalar(
            select(RatingPeriodModel)
            .where(
                RatingPeriodModel.starts_at > starts_at,
                RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
            )
            .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
            .limit(1)
        )
        return model.to_domain() if model is not None else None

    async def get_active_published_version(self, period_id: UUID) -> int | None:
        return await self._session.scalar(
            select(RatingPeriodModel.published_version).where(
                RatingPeriodModel.id == period_id,
                RatingPeriodModel.status == RatingPeriodStatus.PUBLISHED,
            )
        )

    async def list_open_ending_at_or_before(self, ends_at: datetime) -> Sequence[RatingPeriod]:
        models = (
            await self._session.scalars(
                select(RatingPeriodModel)
                .where(
                    RatingPeriodModel.status == RatingPeriodStatus.OPEN,
                    RatingPeriodModel.ends_at <= ends_at,
                )
                .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def list_calculating(self) -> Sequence[RatingPeriod]:
        models = (
            await self._session.scalars(
                select(RatingPeriodModel)
                .where(RatingPeriodModel.status == RatingPeriodStatus.CALCULATING)
                .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def list_non_open_from(self, starts_at: datetime) -> Sequence[RatingPeriod]:
        models = (
            await self._session.scalars(
                select(RatingPeriodModel)
                .where(
                    RatingPeriodModel.starts_at >= starts_at,
                    RatingPeriodModel.status != RatingPeriodStatus.OPEN,
                )
                .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
                .with_for_update()
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def list_non_open_between(
        self, starts_at: datetime, ends_at: datetime
    ) -> Sequence[RatingPeriod]:
        models = (
            await self._session.scalars(
                select(RatingPeriodModel)
                .where(
                    RatingPeriodModel.starts_at >= starts_at,
                    RatingPeriodModel.starts_at <= ends_at,
                    RatingPeriodModel.status != RatingPeriodStatus.OPEN,
                )
                .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def reserve_calculation_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        model = (
            await self._session.scalars(
                update(RatingPeriodModel)
                .where(
                    RatingPeriodModel.id == period_id,
                    RatingPeriodModel.version == expected_version,
                    RatingPeriodModel.calculation_version < calculation_version,
                )
                .values(
                    calculation_version=calculation_version,
                    calculated_version=None,
                    updated_at=func.now(),
                    version=expected_version + 1,
                )
                .returning(RatingPeriodModel)
            )
        ).one_or_none()
        return model.to_domain() if model is not None else None

    async def update_state(
        self,
        *,
        period_id: UUID,
        status: RatingPeriodStatus,
        calculation_version: int,
        published_at: datetime | None,
        expected_version: int,
    ) -> RatingPeriod | None:
        statement = (
            update(RatingPeriodModel)
            .where(
                RatingPeriodModel.id == period_id,
                RatingPeriodModel.version == expected_version,
            )
            .values(
                status=status,
                calculation_version=calculation_version,
                published_at=published_at,
                updated_at=func.now(),
                version=expected_version + 1,
            )
            .returning(RatingPeriodModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def mark_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        statement = (
            update(RatingPeriodModel)
            .where(
                RatingPeriodModel.id == period_id,
                RatingPeriodModel.status == RatingPeriodStatus.CALCULATING,
                RatingPeriodModel.calculation_version == calculation_version,
                RatingPeriodModel.version == expected_version,
            )
            .values(
                calculated_version=calculation_version,
                updated_at=func.now(),
                version=expected_version + 1,
            )
            .returning(RatingPeriodModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def mark_shadow_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None:
        model = (
            await self._session.scalars(
                update(RatingPeriodModel)
                .where(
                    RatingPeriodModel.id == period_id,
                    RatingPeriodModel.status != RatingPeriodStatus.OPEN,
                    RatingPeriodModel.calculation_version == calculation_version,
                    RatingPeriodModel.version == expected_version,
                )
                .values(
                    calculated_version=calculation_version,
                    updated_at=func.now(),
                    version=expected_version + 1,
                )
                .returning(RatingPeriodModel)
            )
        ).one_or_none()
        return model.to_domain() if model is not None else None

    async def mark_published(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None:
        statement = (
            update(RatingPeriodModel)
            .where(
                RatingPeriodModel.id == period_id,
                RatingPeriodModel.status == RatingPeriodStatus.CALCULATING,
                RatingPeriodModel.calculation_version == calculation_version,
                RatingPeriodModel.calculated_version == calculation_version,
                RatingPeriodModel.version == expected_version,
            )
            .values(
                status=RatingPeriodStatus.PUBLISHED,
                published_version=calculation_version,
                published_at=published_at,
                updated_at=func.now(),
                version=expected_version + 1,
            )
            .returning(RatingPeriodModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def activate_rebuild_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None:
        model = (
            await self._session.scalars(
                update(RatingPeriodModel)
                .where(
                    RatingPeriodModel.id == period_id,
                    RatingPeriodModel.status.in_(
                        (RatingPeriodStatus.CALCULATING, RatingPeriodStatus.PUBLISHED)
                    ),
                    RatingPeriodModel.calculation_version == calculation_version,
                    RatingPeriodModel.calculated_version == calculation_version,
                    RatingPeriodModel.version == expected_version,
                )
                .values(
                    status=RatingPeriodStatus.PUBLISHED,
                    published_version=calculation_version,
                    published_at=func.coalesce(RatingPeriodModel.published_at, published_at),
                    updated_at=func.now(),
                    version=expected_version + 1,
                )
                .returning(RatingPeriodModel)
            )
        ).one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyRatingInputRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _model(item: RatingPeriodMatchInput) -> RatingPeriodMatchInputModel:
        return RatingPeriodMatchInputModel(
            period_id=item.period_id,
            calculation_version=item.calculation_version,
            match_id=item.match_id,
            source_match_version=item.source_match_version,
            correction_revision_id=item.correction_revision_id,
            result_fingerprint=item.result_fingerprint,
            player1_id=item.player1_id,
            player2_id=item.player2_id,
            score1=item.score1,
            score2=item.score2,
            completed_at=item.completed_at,
            created_at=item.created_at,
        )

    async def add(self, item: RatingPeriodMatchInput) -> RatingPeriodMatchInput:
        model = self._model(item)
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def add_all(
        self, items: Sequence[RatingPeriodMatchInput]
    ) -> Sequence[RatingPeriodMatchInput]:
        models = [self._model(item) for item in items]
        self._session.add_all(models)
        await self._session.flush()
        return tuple(model.to_domain() for model in models)

    async def add_if_absent(
        self, item: RatingPeriodMatchInput
    ) -> tuple[RatingPeriodMatchInput, bool]:
        values = {
            column.name: getattr(item, column.name)
            for column in RatingPeriodMatchInputModel.__table__.columns
        }
        statement = (
            insert(RatingPeriodMatchInputModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    RatingPeriodMatchInputModel.period_id,
                    RatingPeriodMatchInputModel.calculation_version,
                    RatingPeriodMatchInputModel.match_id,
                ]
            )
            .returning(RatingPeriodMatchInputModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        if model is not None:
            return model.to_domain(), True
        existing = await self.get(item.period_id, item.calculation_version, item.match_id)
        if existing is None:
            raise RuntimeError("Rating input conflict did not return the existing row")
        return existing, False

    async def get(
        self, period_id: UUID, calculation_version: int, match_id: UUID
    ) -> RatingPeriodMatchInput | None:
        model = await self._session.get(
            RatingPeriodMatchInputModel,
            (period_id, calculation_version, match_id),
        )
        return model.to_domain() if model is not None else None

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> Sequence[RatingPeriodMatchInput]:
        models = (
            await self._session.scalars(
                select(RatingPeriodMatchInputModel)
                .where(
                    RatingPeriodMatchInputModel.period_id == period_id,
                    RatingPeriodMatchInputModel.calculation_version == calculation_version,
                )
                .order_by(
                    RatingPeriodMatchInputModel.completed_at,
                    RatingPeriodMatchInputModel.match_id,
                )
            )
        ).all()
        return tuple(model.to_domain() for model in models)


class SqlAlchemyRatingResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _model(result: RatingPeriodResult) -> RatingPeriodResultModel:
        return RatingPeriodResultModel(
            id=result.id,
            period_id=result.period_id,
            calculation_version=result.calculation_version,
            user_id=result.user_id,
            rating_before=result.rating_before,
            rating_after=result.rating_after,
            rd_before=result.rd_before,
            rd_after=result.rd_after,
            volatility_before=result.volatility_before,
            volatility_after=result.volatility_after,
            games_count=result.games_count,
            is_active=result.is_active,
            created_at=result.created_at,
        )

    async def add(self, result: RatingPeriodResult) -> RatingPeriodResult:
        model = self._model(result)
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def add_all(self, results: Sequence[RatingPeriodResult]) -> Sequence[RatingPeriodResult]:
        models = [self._model(result) for result in results]
        self._session.add_all(models)
        await self._session.flush()
        return tuple(model.to_domain() for model in models)

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> Sequence[RatingPeriodResult]:
        models = (
            await self._session.scalars(
                select(RatingPeriodResultModel)
                .where(
                    RatingPeriodResultModel.period_id == period_id,
                    RatingPeriodResultModel.calculation_version == calculation_version,
                )
                .order_by(RatingPeriodResultModel.user_id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def list_for_user(self, user_id: UUID) -> Sequence[RatingPeriodResult]:
        models = (
            await self._session.scalars(
                select(RatingPeriodResultModel)
                .where(RatingPeriodResultModel.user_id == user_id)
                .order_by(
                    RatingPeriodResultModel.period_id.desc(),
                    RatingPeriodResultModel.calculation_version.desc(),
                )
            )
        ).all()
        return tuple(model.to_domain() for model in models)


class SqlAlchemyRatingRecalculationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: RatingRecalculationRequest) -> RatingRecalculationRequest:
        model = RatingRecalculationRequestModel(
            id=request.id,
            from_period_id=request.from_period_id,
            caused_by_match_id=request.caused_by_match_id,
            caused_by_revision_id=request.caused_by_revision_id,
            rebuild_job_id=request.rebuild_job_id,
            status=request.status,
            created_at=request.created_at,
            started_at=request.started_at,
            completed_at=request.completed_at,
            error_code=request.error_code,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get_by_revision(self, revision_id: UUID) -> RatingRecalculationRequest | None:
        model = await self._session.scalar(
            select(RatingRecalculationRequestModel).where(
                RatingRecalculationRequestModel.caused_by_revision_id == revision_id
            )
        )
        return model.to_domain() if model is not None else None

    async def list_by_status(
        self, status: RatingRecalculationStatus
    ) -> Sequence[RatingRecalculationRequest]:
        models = (
            await self._session.scalars(
                select(RatingRecalculationRequestModel)
                .where(RatingRecalculationRequestModel.status == status)
                .order_by(
                    RatingRecalculationRequestModel.created_at,
                    RatingRecalculationRequestModel.id,
                )
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def list_for_job(self, rebuild_job_id: UUID) -> Sequence[RatingRecalculationRequest]:
        models = (
            await self._session.scalars(
                select(RatingRecalculationRequestModel)
                .where(RatingRecalculationRequestModel.rebuild_job_id == rebuild_job_id)
                .order_by(
                    RatingRecalculationRequestModel.created_at,
                    RatingRecalculationRequestModel.id,
                )
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def link_to_job(
        self,
        *,
        request_id: UUID,
        rebuild_job_id: UUID,
        expected_rebuild_job_id: UUID | None,
    ) -> RatingRecalculationRequest | None:
        expected = (
            RatingRecalculationRequestModel.rebuild_job_id.is_(None)
            if expected_rebuild_job_id is None
            else RatingRecalculationRequestModel.rebuild_job_id == expected_rebuild_job_id
        )
        statement = (
            update(RatingRecalculationRequestModel)
            .where(RatingRecalculationRequestModel.id == request_id, expected)
            .values(rebuild_job_id=rebuild_job_id)
            .returning(RatingRecalculationRequestModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None

    async def update_status(
        self,
        *,
        request_id: UUID,
        status: RatingRecalculationStatus,
        started_at: datetime | None,
        completed_at: datetime | None,
        error_code: str | None,
    ) -> RatingRecalculationRequest | None:
        statement = (
            update(RatingRecalculationRequestModel)
            .where(RatingRecalculationRequestModel.id == request_id)
            .values(
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                error_code=error_code,
            )
            .returning(RatingRecalculationRequestModel)
        )
        model = (await self._session.scalars(statement)).one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyRatingRebuildJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: RatingRebuildJob) -> RatingRebuildJob:
        model = RatingRebuildJobModel(
            id=job.id,
            from_period_id=job.from_period_id,
            through_period_id=job.through_period_id,
            status=job.status,
            singleton_scope=job.singleton_scope,
            created_at=job.created_at,
            started_at=job.started_at,
            ready_at=job.ready_at,
            completed_at=job.completed_at,
            error_code=job.error_code,
            version=job.version,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get(self, job_id: UUID, *, for_update: bool = False) -> RatingRebuildJob | None:
        statement = select(RatingRebuildJobModel).where(RatingRebuildJobModel.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_active(self, *, for_update: bool = False) -> RatingRebuildJob | None:
        statement = (
            select(RatingRebuildJobModel)
            .where(RatingRebuildJobModel.status.in_(_ACTIVE_REBUILD_STATUSES))
            .order_by(RatingRebuildJobModel.created_at, RatingRebuildJobModel.id)
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def get_publication_barrier(self) -> RatingRebuildJob | None:
        return await self.get_active()

    async def update_status(
        self,
        *,
        job_id: UUID,
        status: RatingRebuildStatus,
        expected_version: int,
        completed_at: datetime | None,
        error_code: str | None,
        started_at: datetime | None = None,
        ready_at: datetime | None = None,
    ) -> RatingRebuildJob | None:
        values: dict[str, object] = {
            "status": status,
            "completed_at": completed_at,
            "error_code": error_code,
            "version": expected_version + 1,
        }
        if started_at is not None:
            values["started_at"] = started_at
        if ready_at is not None:
            values["ready_at"] = ready_at
        model = (
            await self._session.scalars(
                update(RatingRebuildJobModel)
                .where(
                    RatingRebuildJobModel.id == job_id,
                    RatingRebuildJobModel.version == expected_version,
                )
                .values(**values)
                .returning(RatingRebuildJobModel)
            )
        ).one_or_none()
        return model.to_domain() if model is not None else None


class SqlAlchemyRatingRebuildPeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, checkpoint: RatingRebuildPeriod) -> RatingRebuildPeriod:
        model = RatingRebuildPeriodModel(
            rebuild_job_id=checkpoint.rebuild_job_id,
            period_id=checkpoint.period_id,
            source_version=checkpoint.source_version,
            target_version=checkpoint.target_version,
            status=checkpoint.status,
            created_at=checkpoint.created_at,
            completed_at=checkpoint.completed_at,
            error_code=checkpoint.error_code,
        )
        self._session.add(model)
        await self._session.flush()
        return model.to_domain()

    async def get(
        self,
        rebuild_job_id: UUID,
        period_id: UUID,
        *,
        for_update: bool = False,
    ) -> RatingRebuildPeriod | None:
        statement = select(RatingRebuildPeriodModel).where(
            RatingRebuildPeriodModel.rebuild_job_id == rebuild_job_id,
            RatingRebuildPeriodModel.period_id == period_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        return model.to_domain() if model is not None else None

    async def list_for_job(self, rebuild_job_id: UUID) -> Sequence[RatingRebuildPeriod]:
        models = (
            await self._session.scalars(
                select(RatingRebuildPeriodModel)
                .join(
                    RatingPeriodModel,
                    RatingPeriodModel.id == RatingRebuildPeriodModel.period_id,
                )
                .where(RatingRebuildPeriodModel.rebuild_job_id == rebuild_job_id)
                .order_by(RatingPeriodModel.starts_at, RatingPeriodModel.id)
            )
        ).all()
        return tuple(model.to_domain() for model in models)

    async def update_status(
        self,
        *,
        rebuild_job_id: UUID,
        period_id: UUID,
        status: RatingRebuildPeriodStatus,
        expected_status: RatingRebuildPeriodStatus,
        completed_at: datetime | None,
        error_code: str | None,
    ) -> RatingRebuildPeriod | None:
        model = (
            await self._session.scalars(
                update(RatingRebuildPeriodModel)
                .where(
                    RatingRebuildPeriodModel.rebuild_job_id == rebuild_job_id,
                    RatingRebuildPeriodModel.period_id == period_id,
                    RatingRebuildPeriodModel.status == expected_status,
                )
                .values(
                    status=status,
                    completed_at=completed_at,
                    error_code=error_code,
                )
                .returning(RatingRebuildPeriodModel)
            )
        ).one_or_none()
        return model.to_domain() if model is not None else None
