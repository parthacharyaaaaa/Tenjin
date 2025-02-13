import os

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from resource_server.flask_config import FLASK_CONFIG_OBJECT

from resource_server.blueprint_forum import forum
from resource_server.blueprint_admin import admin
from resource_server.blueprint_user import user
from resource_server.blueprint_config import config

app = Flask(import_name="RS",
            instance_path=os.path.join(os.path.dirname(__file__), "instance"))

app.config.from_object(FLASK_CONFIG_OBJECT)
app.register_blueprint(forum)
app.register_blueprint(admin)
app.register_blueprint(user)
app.register_blueprint(config)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

