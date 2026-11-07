"""Expected match use-case failures."""

from pingstats.core.exceptions import ApplicationError


class MatchNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(code="match_not_found", message="Match not found", status_code=404)


class MatchUserNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(code="match_user_not_found", message="User not found", status_code=404)


class SelfMatchError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="match_with_self",
            message="A player cannot create a match with themselves",
            status_code=400,
        )


class MatchPermissionError(ApplicationError):
    def __init__(self, message: str = "This match operation is forbidden") -> None:
        super().__init__(code="match_forbidden", message=message, status_code=403)


class MatchStateError(ApplicationError):
    def __init__(self, message: str = "Match state does not allow this operation") -> None:
        super().__init__(code="match_state_conflict", message=message, status_code=409)


class MatchVersionConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="match_version_conflict",
            message="Match was changed by another request",
            status_code=409,
        )


class InvalidScoreError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="match_invalid_score", message=message, status_code=400)


class CorrectionWindowExpiredError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="match_correction_window_expired",
            message="The 24-hour correction window has expired",
            status_code=403,
        )
