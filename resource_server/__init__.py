import os
import orjson
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from resource_server.flask_config import FLASK_CONFIG_OBJECT
from sqlalchemy import MetaData

APP_CTX_CWD : os.PathLike = os.path.dirname(__file__)

def create_app() -> Flask:
    global APP_CTX_CWD
    app = Flask(import_name="RS",
                instance_path=os.path.join(APP_CTX_CWD, "instance"))
    app.config.from_object(FLASK_CONFIG_OBJECT)

    ### Database setup ###

    from resource_server.models import db, migrate
    db.init_app(app)
    migrate.init_app(app)

    ### Blueprints registaration ###
    from resource_server.blueprint_forum import forum
    from resource_server.blueprint_admin import admin
    from resource_server.blueprint_user import user
    from resource_server.blueprint_config import config
    from resource_server.blueprint_posts import post
    app.register_blueprint(forum)
    app.register_blueprint(admin)
    app.register_blueprint(user)
    app.register_blueprint(config)
    app.register_blueprint(post)

    return app

from resource_server.models import *