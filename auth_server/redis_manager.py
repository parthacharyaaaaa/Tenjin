from redis import Redis
from flask import Flask

SyncedStore: Redis = None
RedisInterface: Redis = None

def init_redis(app: Flask) -> None:
    global RedisInterface
    '''Initialize a Redis instance based on a Flask app's "REDIS_KWARGS" config key'''
    RedisInterface = Redis(**app.config['REDIS_KWARGS'])

    if not RedisInterface.ping():
        raise ConnectionError('Failed to connect to Redis instance')

def init_syncedstore(app: Flask) -> None:
    global SyncedStore
    '''Initialize a Redis instance based on a Flask app's "REDIS_SYNCED_STORE_KWARGS" config key'''
    SyncedStore = Redis(**app.config['REDIS_SYNCED_STORE_KWARGS'])

    if not SyncedStore.ping():
        raise ConnectionError('Failed to connect to Redis instance')