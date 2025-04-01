from redis import Redis
from flask import Flask

RedisInterface = None

def init_redis(app: Flask):
    global RedisInterface
    RedisInterface = Redis(host=app.config["REDIS_HOST"],
                                  port=app.config["REDIS_PORT"],
                                  decode_responses=True)
    if not RedisInterface.ping():
        raise ConnectionError()