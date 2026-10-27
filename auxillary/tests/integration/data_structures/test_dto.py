from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

import orjson
import pytest
import pytest_asyncio
from auxillary.data_structures.dto import INT64_MAX, AbstractResult, int64
from auxillary.utils import cache_repr
from redis.asyncio.client import Redis


class RedisResult(AbstractResult):
    resource_name: ClassVar[str] = "redis-result"

    id_: int64
    score: Decimal
    ratio: float
    enabled: bool
    label: str
    created_at: datetime
    release_date: date
    identifier: UUID
    tags: tuple[str, ...]
    payload: dict[str, object]
    optional_note: str | None = None


@pytest_asyncio.fixture
async def dto_cache_key(async_redis_client: Redis) -> AsyncIterator[str]:
    cache_key = "test:auxillary:dto"
    await async_redis_client.delete(cache_key)
    try:
        yield cache_key
    finally:
        await async_redis_client.delete(cache_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    (
        RedisResult(
            id_=1,
            score=Decimal("0.00"),
            ratio=-0.125,
            enabled=False,
            label='quotes: "value"\nsecond line',
            created_at=datetime(2026, 10, 26, 12, 34, 56, 789012, tzinfo=UTC),
            release_date=date(2026, 10, 26),
            identifier=UUID("12345678-1234-5678-1234-567812345678"),
            tags=(),
            payload={"nested": {"items": [1, False, None]}},
        ),
        RedisResult(
            id_=INT64_MAX,
            score=Decimal("99999999999999999999.00000001"),
            ratio=1.5,
            enabled=True,
            label="",
            created_at=datetime(1970, 1, 1, tzinfo=UTC),
            release_date=date(1970, 1, 1),
            identifier=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            tags=("alpha", "beta"),
            payload={"empty_list": [], "empty_mapping": {}},
            optional_note="present",
        ),
    ),
)
async def test_redis_hash_round_trip(
    async_redis_client: Redis,
    dto_cache_key: str,
    result: RedisResult,
) -> None:
    await async_redis_client.hset(dto_cache_key, mapping=cache_repr(result))

    cached = await async_redis_client.hgetall(dto_cache_key)

    assert RedisResult.construct_from_cache(cached) == result
    assert await async_redis_client.hlen(dto_cache_key) == len(RedisResult.model_fields)


@pytest.mark.asyncio
async def test_redis_can_read_and_update_individual_serialized_fields(
    async_redis_client: Redis,
    dto_cache_key: str,
) -> None:
    result = RedisResult(
        id_=7,
        score=Decimal("12.50"),
        ratio=0.5,
        enabled=True,
        label="before",
        created_at=datetime(2026, 10, 26, tzinfo=UTC),
        release_date=date(2026, 10, 26),
        identifier=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        tags=("cached",),
        payload={"version": 1},
    )
    await async_redis_client.hset(dto_cache_key, mapping=cache_repr(result))

    assert orjson.loads(await async_redis_client.hget(dto_cache_key, "label")) == (
        "before"
    )

    await async_redis_client.hincrby(dto_cache_key, "id_", 5)
    await async_redis_client.hset(
        dto_cache_key,
        mapping={
            "label": orjson.dumps("after"),
            "payload": orjson.dumps({"version": 2, "updated": True}),
            "optional_note": orjson.dumps("set through Redis"),
        },
    )

    reconstructed = RedisResult.construct_from_cache(
        await async_redis_client.hgetall(dto_cache_key)
    )

    assert reconstructed == result.model_copy(
        update={
            "id_": 12,
            "label": "after",
            "payload": {"version": 2, "updated": True},
            "optional_note": "set through Redis",
        }
    )
