from contextlib import asynccontextmanager
from redis.asyncio import Redis


@asynccontextmanager
async def locked_operation(redis: Redis, lock_name: str):
    try:
        yield
    finally:
        await redis.delete(lock_name)
