"""Expected friendship use-case failures."""

from pingstats.core.exceptions import ApplicationError


class FriendshipApplicationError(ApplicationError):
    """Base class for stable friendship API errors."""


class SelfFriendRequestError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="friend_request_to_self",
            message="A friend request cannot be sent to yourself",
            status_code=400,
        )


class FriendshipUserNotFoundError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="friendship_user_not_found",
            message="User not found",
            status_code=404,
        )


class FriendRequestNotFoundError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="friend_request_not_found",
            message="Friend request not found",
            status_code=404,
        )


class FriendshipNotFoundError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="friendship_not_found",
            message="Friendship not found",
            status_code=404,
        )


class PendingFriendRequestExistsError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="pending_friend_request_exists",
            message="A pending friend request already exists between these users",
            status_code=409,
        )


class AlreadyFriendsError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="already_friends",
            message="Users are already friends",
            status_code=409,
        )


class FriendRequestStateError(FriendshipApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="friend_request_not_pending",
            message="Friend request is no longer pending",
            status_code=409,
        )


class FriendRequestPermissionError(FriendshipApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(
            code="friend_request_forbidden",
            message=message,
            status_code=403,
        )
