from redis import Redis
from flask import Flask
from typing import Iterable

RedisInterface: Redis = None

def hset_with_ttl(interface: Redis, name: str, mapping: dict, ttl: int, transaction: bool = True):
    with interface.pipeline(transaction) as pp:
        pp.hset(name=name, mapping=mapping)
        pp.expire(name=name, time=ttl)
        pp.execute()

def batch_hset_with_ttl(interface: Redis, names: Iterable[str], mappings: Iterable[dict], ttl: int, transaction: bool = True):
    if len(names) != len(mappings):
        raise ValueError('Names and mappings do not match')
    
    with interface.pipeline(transaction) as pp:
        for idx, mapping in enumerate(mappings):
            pp.hset(name=names[idx], mapping=mapping)
            pp.expire(name=names[idx], time=ttl)
        pp.execute()

def init_redis(**constructor_kwargs):
    global RedisInterface
    RedisInterface = Redis(**constructor_kwargs)
    if not RedisInterface.ping():
        raise ConnectionError()