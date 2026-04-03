from redis import Redis

__all__ = (
    "RedisInterface",
    "init_redis",
)

RedisInterface: Redis | None = None


def init_redis(**constructor_kwargs):
    global RedisInterface
    RedisInterface = Redis(**constructor_kwargs)
    if not RedisInterface.ping():
        raise ConnectionError()
