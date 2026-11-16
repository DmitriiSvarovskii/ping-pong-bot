"""Expected rating-intake application failures."""

from pingstats.core.exceptions import ApplicationError


class RatingPeriodUnavailableError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_period_unavailable",
            message="The rating period no longer accepts match results",
            status_code=409,
        )


class RatingInputConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_input_conflict",
            message="A different rating snapshot already exists for this match",
            status_code=409,
        )


class InvalidRatingMatchError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="rating_match_invalid", message=message, status_code=409)


class RatingPeriodNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_period_not_found",
            message="Rating period was not found",
            status_code=404,
        )


class RatingNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_not_found",
            message="The player does not have published rating history",
            status_code=404,
        )


class RatingPeriodNotCalculatingError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_period_not_calculating",
            message="Rating period is not ready for calculation",
            status_code=409,
        )


class RatingHistoryUnavailableError(ApplicationError):
    def __init__(self, message: str = "Previous rating period results are unavailable") -> None:
        super().__init__(
            code="rating_history_unavailable",
            message=message,
            status_code=409,
        )


class UnsupportedRatingAlgorithmError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="unsupported_rating_algorithm",
            message="Rating period algorithm snapshot is not supported",
            status_code=409,
        )


class RatingCalculationVersionConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_calculation_version_conflict",
            message="Rating period calculation version has changed",
            status_code=409,
        )


class RatingCalculationSnapshotConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_calculation_snapshot_conflict",
            message="Stored rating results differ from the calculated snapshot",
            status_code=409,
        )


class RatingPeriodNotPublishableError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_period_not_publishable",
            message="Rating period does not have a completed calculation",
            status_code=409,
        )


class RatingPublicationChronologyError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_publication_chronology_conflict",
            message="Rating periods must be published in chronological order",
            status_code=409,
        )


class RatingPublicationSnapshotConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_publication_snapshot_conflict",
            message="Stored rating results are inconsistent with published rating state",
            status_code=409,
        )


class RatingCorrectionHistoryError(ApplicationError):
    def __init__(self, message: str = "The rated match has no source rating input") -> None:
        super().__init__(
            code="rating_correction_history_conflict",
            message=message,
            status_code=409,
        )


class RatingRebuildBarrierError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="rating_rebuild_in_progress",
            message="Rating history is waiting for a coordinated rebuild",
            status_code=409,
        )


class RatingRebuildExecutionError(ApplicationError):
    def __init__(
        self, message: str = "Rating rebuild cannot continue from its current state"
    ) -> None:
        super().__init__(
            code="rating_rebuild_execution_conflict",
            message=message,
            status_code=409,
        )


class RatingCutoverConflictError(ApplicationError):
    def __init__(self, message: str = "Rating rebuild is not ready for atomic cutover") -> None:
        super().__init__(
            code="rating_cutover_conflict",
            message=message,
            status_code=409,
        )
