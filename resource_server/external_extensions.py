from redis import Redis
from flask import Flask

RedisInterface = None

def hset_with_ttl(interface: Redis, name: str, mapping: dict, ttl: int, transaction: bool = True):
    with interface.pipeline(transaction) as pp:
        pp.hset(name=name, mapping=mapping)
        pp.expire(name=name, time=ttl)
        pp.execute()

def init_redis(app: Flask):
    global RedisInterface
    RedisInterface = Redis(host=app.config["REDIS_HOST"],
                                  port=app.config["REDIS_PORT"],
                                  decode_responses=True)
    if not RedisInterface.ping():
        raise ConnectionError()