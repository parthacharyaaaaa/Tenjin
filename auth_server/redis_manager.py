from redis import Redis
from flask import Flask

SyncedStore: Redis = None
RedisInterface: Redis = None

def init_redis(**constructor_kwargs) -> None:
    global RedisInterface
    '''Initialize a Redis instance based on a Flask app's "REDIS_KWARGS" config key'''
    RedisInterface = Redis(**constructor_kwargs)

    if not RedisInterface.ping():
        raise ConnectionError('Failed to connect to Redis instance')

def init_syncedstore(**constructor_kwargs) -> None:
    global SyncedStore
    '''Initialize a Redis instance based on a Flask app's "REDIS_SYNCED_STORE_KWARGS" config key'''
    SyncedStore = Redis(**constructor_kwargs)

    if not SyncedStore.ping():
        raise ConnectionError('Failed to connect to Redis instance')