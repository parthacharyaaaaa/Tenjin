import os
import threading
from flask import Flask
from flask_migrate import Migrate

from sqlalchemy import text
from resource_server.flask_config import FLASK_CONFIG_OBJECT
from auxillary.utils import generic_error_handler
from types import MappingProxyType
from typing import Any, Final
import toml

from resource_server import blueprints
from resource_server.models import db
from resource_server.resource_auxillary import distributed_create_db

__all__ = ("APP_CTX_CWD", "create_app")

APP_CTX_CWD: Final[str] = os.path.dirname(__file__)


def create_app() -> Flask:
    global APP_CTX_CWD
    app = Flask(
        import_name="RS",
        instance_path=os.path.join(APP_CTX_CWD, "instance"),
        static_folder=os.path.join(APP_CTX_CWD, "static"),
    )

    app.config.from_object(FLASK_CONFIG_OBJECT)
    app.cli.name = "RS"
    app.register_error_handler(Exception, generic_error_handler)

    ### Database setup ###
    db.init_app(app)
    migrate = Migrate(app, db)

    ### Redis ###
    redis_config_fpath: str = os.path.join(
        APP_CTX_CWD, "config", os.environ["redis_config_filename"]
    )
    if not os.path.isfile(redis_config_fpath):
        raise FileNotFoundError("Redis config toml file not found")

    redis_config_kwargs: dict[str, Any] = toml.load(f=redis_config_fpath)
    redis_config_kwargs.update(
        {
            "host": os.environ["RESOURCE_SERVER_REDIS_HOST"],
            "port": os.environ["RESOURCE_SERVER_REDIS_PORT"],
            "db": os.environ["RESOURCE_SERVER_REDIS_DB"],
            "username": os.environ["RESOURCE_SERVER_REDIS_USERNAME"],
            "password": os.environ["RESOURCE_SERVER_REDIS_PASSWORD"],
        }
    )  # Inject login credentials through env

    from resource_server.external_extensions import init_redis

    init_redis(**redis_config_kwargs)

    ### Blueprints registaration ###
    for blueprint, prefix in blueprints.PREFIX_MAPPING.items():
        app.register_blueprint(
            blueprint, url_prefix="/".join((app.config["APPLICATION_ROOT"], prefix))
        )

    from resource_server.redis_config import RedisConfig
    from resource_server.external_extensions import RedisInterface
    from resource_server.resource_auxillary import update_jwks, background_poll

    assert RedisInterface
    background_poller: threading.Thread = threading.Thread(
        target=background_poll,
        daemon=True,
        kwargs={
            "current_app": app,
            "interface": RedisInterface,
            "lock_ttl": RedisConfig.ANNOUNCEMENT_DURATION,
            "interval": RedisConfig.TTL_STRONG,
        },
    )
    # Initial JWKS load
    jwks_mapping: dict[str, str | int] = RedisInterface.hgetall("JWKS_MAPPING")
    if not jwks_mapping:
        # updaet_jwks() already handles race conditions among multiple workers trying to update JWKS mapping, so no need to have separate logic here
        jwks_mapping = update_jwks(
            endpoint=f"{app.config['AUTH_SERVER_URL']}/auth/jwks.json",
            currentMapping={},
            interface=RedisInterface,
            lock_ttl=RedisConfig.ANNOUNCEMENT_DURATION,
            jwks_poll_cooldown=RedisConfig.JWKS_POLL_COOLDOWN,
        )
        if not jwks_mapping:
            raise RuntimeError("Failed to initialize JWKS mapping in Redis")
    app.config["KEY_VK_MAPPING"] = jwks_mapping

    background_poller.start()

    # Load genres into config
    with app.app_context():
        distributed_create_db(client=RedisInterface,
                              sqlalchemy=db)
        with db.engine.connect() as conn:
            GENRES: tuple[tuple[str, str]] = tuple(conn.execute(text("SELECT _name, id FROM genres;")).fetchall())  # type: ignore[reportAssignmentType]
            app.config["GENRES"] = MappingProxyType(
                {genre[0]: genre[1] for genre in GENRES}
            )

    return app


from resource_server.models import *
