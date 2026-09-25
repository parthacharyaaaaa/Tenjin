from dataclasses import dataclass, field
from types import TracebackType
from typing import Self
from uuid import uuid4

from redis.asyncio.client import Redis
from redis.commands.core import AsyncScript

from auxillary.data_structures.locks.lua_scripts import (
    CONDITIONAL_LOCK_UNSETTING_SCRIPT,
)
from auxillary.data_structures.locks.typing import SupportsDistributedRelease
from auxillary.singleton import SingletonMetaclass


@dataclass(slots=True, frozen=True)
class BasicLockContext:
    resource: str
    value: str
    client: SupportsDistributedRelease
    valid: bool

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.valid:
            await self.client.release(self.resource, self.valid)


@dataclass(slots=True, weakref_slot=True, frozen=True)
class RedisInstanceLockFactory(metaclass=SingletonMetaclass):
    redis_client: Redis
    _registrered_release_script: AsyncScript = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_registrered_release_script",
            self.redis_client.register_script(CONDITIONAL_LOCK_UNSETTING_SCRIPT),
        )

    @staticmethod
    def generate_random_lock_token() -> str:
        return uuid4().hex

    async def lock(
        self, resource: str, value: str, ttl: int, *, ttl_in_ms: bool = False
    ) -> BasicLockContext:
        if value is None:
            value = self.generate_random_lock_token()
        if not ttl_in_ms:
            return BasicLockContext(
                resource,
                value,
                self,
                bool(await self.redis_client.set(resource, value, ex=ttl, nx=True)),
            )
        return BasicLockContext(
            resource,
            value,
            self,
            bool(await self.redis_client.set(resource, value, px=ttl, nx=True)),
        )

    async def release(self, lock_name: str, value: int) -> None:
        await self.redis_client.evalsha(  # pyrefly: ignore[not-async]
            self._registrered_release_script.sha, 1, lock_name, value
        )
