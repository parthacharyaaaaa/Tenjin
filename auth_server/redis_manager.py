from redis import Redis
from flask import Flask

RedisInterface: Redis = None

def init_redis(app: Flask) -> None:
    global RedisInterface
    '''Initialize a Redis instance based on a Flask app's "REDIS_KWARGS" config key'''
    RedisInterface = Redis(**app.config['REDIS_KWARGS'])

    if not RedisInterface.ping():
        raise ConnectionError('Failed to connect to Redis instance')