from typing import TypedDict

XInfoGroupResponse = TypedDict(
    "XInfoGroupResponse",
    {
        "name": bytes,
        "consumers": int,
        "pending": int,
        "last-delivered-id": bytes,
        "entries-read": int | None,
        "lag": int | None,
    },
)


class XPendingRangeResponse(TypedDict):
    message_id: str
    consumer: str
    time_since_delivered: int
    times_delivered: int
