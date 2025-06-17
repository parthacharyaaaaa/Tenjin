from redis import Redis
from flask import Flask
from typing import Iterable

RedisInterface: Redis = None

def init_redis(**constructor_kwargs):
    global RedisInterface
    RedisInterface = Redis(**constructor_kwargs)
    if not RedisInterface.ping():
        raise ConnectionError()