from dataclasses import dataclass, field
from datetime import timedelta

from redis.asyncio.client import Redis


@dataclass(slots=True, frozen=True)
class HashmapIdempotencyManager:
    redis_client: Redis
    idempotency_guard_duration_s: timedelta
    hashmap_name: str = field(default="__idempotency_set__")
    entry_value: str = field(default="x")

    async def check_idempotency(self, identifier: str) -> bool:
        return bool(
            await self.redis_client.hexists(  # pyrefly: ignore[not-async]
                self.hashmap_name, identifier
            )
        )

    async def set_idempotent_entry(
        self, identifier: str, *, duration_s: timedelta | None = None
    ) -> bool:
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            pipeline.hsetnx(  # pyrefly: ignore[not-async]
                self.hashmap_name,
                identifier,
                self.entry_value,
            )
            pipeline.hexpire(
                self.hashmap_name,
                int((duration_s or self.idempotency_guard_duration_s).total_seconds()),
                identifier,
                nx=True,  # God-tier kwarg omg
            )
            result = (await pipeline.execute())[0]
        return bool(result)
