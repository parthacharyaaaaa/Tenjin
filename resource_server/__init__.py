import os
import orjson
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from resource_server.flask_config import FLASK_CONFIG_OBJECT
from sqlalchemy import MetaData

from resource_server.blueprint_forum import forum
from resource_server.blueprint_admin import admin
from resource_server.blueprint_user import user
from resource_server.blueprint_config import config
from resource_server.blueprint_posts import post

APP_CTX_CWD : os.PathLike = os.path.dirname(__file__)

app = Flask(import_name="RS",
            instance_path=os.path.join(APP_CTX_CWD, "instance"))

### Database setup ###
CONFIG : dict = {}
with open(os.path.join(app.instance_path, "config.json"), "rb") as configFile:
    CONFIG = orjson.loads(configFile.read())
    METADATA = MetaData(naming_convention=CONFIG["database"]["naming_convention"])

db = SQLAlchemy(app, metadata=METADATA)
migrate = Migrate(app, db)

### Blueprints registaration ###
app.config.from_object(FLASK_CONFIG_OBJECT)
app.register_blueprint(forum)
app.register_blueprint(admin)
app.register_blueprint(user)
app.register_blueprint(config)
app.register_blueprint(post)