from typing import Any, Protocol

from redis.asyncio.client import Pipeline

__all__ = ("SupportsJSON", "SupportsAsyncRedis")


class SupportsJSON(Protocol):
    def __json_like__(self) -> dict[str, Any]: ...


class SupportsAsyncRedis(Protocol):
    async def delete(self, *names: str) -> int: ...
    async def exists(self, *names: str) -> int: ...
    async def expire(self, name: str, time: int) -> bool: ...
    async def ttl(self, name: str) -> int: ...
    async def persist(self, name: str) -> bool: ...
    async def rename(self, src: str, dst: str) -> bool: ...
    async def keys(self, pattern: str = "*") -> list[str]: ...

    async def get(self, name: str) -> str | None: ...
    async def set(
        self,
        name: str,
        value: Any,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None: ...

    async def incr(self, name: str, amount: int = 1) -> int: ...
    async def decr(self, name: str, amount: int = 1) -> int: ...
    async def mget(self, keys: list[str]) -> list[str | None]: ...

    async def hget(
        self,
        name: str,
        key: str,
    ) -> str | None: ...

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: Any | None = None,
        mapping: dict[str, Any] | None = None,
    ) -> int: ...

    async def hdel(
        self,
        name: str,
        *keys: str,
    ) -> int: ...

    async def hexists(
        self,
        name: str,
        key: str,
    ) -> bool: ...

    async def hkeys(
        self,
        name: str,
    ) -> list[str]: ...

    async def hvals(
        self,
        name: str,
    ) -> list[str]: ...

    async def hlen(
        self,
        name: str,
    ) -> int: ...

    async def hgetall(
        self,
        name: str,
    ) -> dict[str, str]: ...

    async def hmget(
        self,
        name: str,
        keys: list[str],
    ) -> list[str | None]: ...

    async def hincrby(
        self,
        name: str,
        key: str,
        amount: int = 1,
    ) -> int: ...

    async def xadd(
        self,
        name: str,
        fields: dict[str, Any],
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str: ...

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[Any]: ...

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ) -> list[Any]: ...

    async def xread(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[Any] | None: ...

    async def xdel(
        self,
        name: str,
        *ids: str,
    ) -> int: ...

    async def xlen(
        self,
        name: str,
    ) -> int: ...

    async def xtrim(
        self,
        name: str,
        maxlen: int,
        approximate: bool = True,
    ) -> int: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        mkstream: bool = False,
    ) -> bool: ...

    async def xgroup_destroy(
        self,
        name: str,
        groupname: str,
    ) -> int: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[Any] | None: ...

    async def xack(
        self,
        name: str,
        groupname: str,
        *ids: str,
    ) -> int: ...

    def pipeline(self, transaction: bool = False) -> Pipeline: ...
