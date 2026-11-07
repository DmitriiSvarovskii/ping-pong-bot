"""Expected game session use-case failures."""

from pingstats.core.exceptions import ApplicationError


class GameSessionNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_not_found", message="Game session not found", status_code=404
        )


class GameSessionUserNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_user_not_found", message="User not found", status_code=404
        )


class GameSessionPermissionError(ApplicationError):
    def __init__(self, message: str = "This game session operation is forbidden") -> None:
        super().__init__(code="game_session_forbidden", message=message, status_code=403)


class GameSessionStateError(ApplicationError):
    def __init__(self, message: str = "Game session state does not allow this operation") -> None:
        super().__init__(code="game_session_state_conflict", message=message, status_code=409)


class ActiveSessionParticipantExistsError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_participant_exists",
            message="User is already an active participant",
            status_code=409,
        )


class ActiveSessionParticipantNotFoundError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_participant_not_found",
            message="Active participation was not found",
            status_code=409,
        )


class GameSessionHasActiveMatchError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_has_active_match",
            message="The operation is blocked by an active session match",
            status_code=409,
        )


class InvalidGameSessionSettingsError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="game_session_invalid_settings", message=message, status_code=400)


class GameSessionQueueNotInitializedError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_queue_not_initialized",
            message="The game session queue is not initialized",
            status_code=409,
        )


class GameSessionQueueConflictError(ApplicationError):
    def __init__(self, message: str = "The game session queue state is invalid") -> None:
        super().__init__(code="game_session_queue_conflict", message=message, status_code=409)


class GameSessionCurrentPlayerLeaveError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="game_session_current_player_leave_forbidden",
            message="A current player cannot leave before the game is completed or cancelled",
            status_code=409,
        )
