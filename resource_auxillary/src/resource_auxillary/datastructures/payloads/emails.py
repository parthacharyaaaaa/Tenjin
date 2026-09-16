from typing import NotRequired, TypedDict


class UserEmailPayload(TypedDict):
    recipient: str
    sender: NotRequired[str]
    body_kwargs: dict[str, str]
