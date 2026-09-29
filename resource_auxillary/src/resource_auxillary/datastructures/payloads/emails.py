from datetime import datetime
from typing import Any, NotRequired, TypedDict


class UserEmailPayload(TypedDict):
    recipient: str
    sender: NotRequired[str]
    body_kwargs: dict[str, Any]


class UserRecoveryEmailPayload(TypedDict):
    username: str
    time_of_request: datetime
    url_token: str


class UserDeletionEmail(TypedDict):
    username: str
    time_of_request: datetime


class UserRegistrationEmail(TypedDict):
    username: str
    profile_url: str
