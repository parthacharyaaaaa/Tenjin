from types import TracebackType
from typing import Protocol, Self


class SupportsBasicLockContext(Protocol):
    @property
    def resource(self) -> str: ...
    @property
    def value(self) -> str: ...
    @property
    def client(self) -> "SupportsDistributedRelease": ...
    @property
    def valid(self) -> bool: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


class SupportsDistributedRelease(Protocol):
    # async def lock(
    # self, resource: str, value: str, ttl: int
    # ) -> SupportsBasicLockContext: ...
    async def release(self, lock_name: str, value: int) -> None: ...
