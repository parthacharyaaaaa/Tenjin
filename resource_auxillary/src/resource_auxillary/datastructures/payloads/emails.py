from typing import TypedDict, NotRequired


class UserEmailPayload(TypedDict):
    recipient: str
    sender: NotRequired[str]
    body_kwargs: dict[str, str]
