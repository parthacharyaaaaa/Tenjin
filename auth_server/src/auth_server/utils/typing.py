from typing import TypedDict, NotRequired, Protocol
from collections.abc import Buffer


class _SupportsDigest(Protocol):
    def digest(self) -> bytes: ...


class AdminSessionDict(TypedDict):
    admin_id: int
    session_id: int
    revival_digest: NotRequired[str]
    session_iteration: int
    role: str
    epoch_timestamp: float
    expiry_timestamp: float


class HashFunc(Protocol):
    def __call__(
        self,
        data: Buffer = b"",
        *,
        usedforsecurity: bool = True,
        string: Buffer | None = None,
    ) -> _SupportsDigest: ...
