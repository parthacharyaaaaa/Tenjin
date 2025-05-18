from flask import Flask
from auth_server.config import flaskconfig
from auxillary.utils import generic_error_handler

def create_app() -> Flask:
    auth_app = Flask('auth_app')
    auth_app.config.from_object(flaskconfig)

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # Extensions
    from auth_server.redis_manager import init_redis
    init_redis(auth_app)
    
    from auth_server.token_manager import init_token_manager
    from auth_server.redis_manager import RedisInterface
    init_token_manager(auth_app, RedisInterface)

    # Blueprints
    from auth_server.blueprint_routes import auth
    auth_app.register_blueprint(auth)
    return auth_app