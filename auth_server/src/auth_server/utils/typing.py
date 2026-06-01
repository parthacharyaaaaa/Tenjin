from typing import TypedDict, NotRequired


class AdminSessionDict(TypedDict):
    admin_id: int
    session_id: int
    revival_digest: NotRequired[str]
    session_iteration: int
    role: str
    epoch_timestamp: float
    expiry_timestamp: float
