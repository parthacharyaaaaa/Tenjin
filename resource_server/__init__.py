import os

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from resource_server.flask_config import FLASK_CONFIG_OBJECT

app = Flask(import_name="RS",
            instance_path=os.path.join(os.path.dirname(__file__), "instance"))

app.config.from_object(FLASK_CONFIG_OBJECT)

db = SQLAlchemy(app)
migrate = Migrate(app, db)

