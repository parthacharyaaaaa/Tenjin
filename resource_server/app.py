import os
from flask import Flask
from flask_migrate import Migrate
from traceback import format_exc
from resource_server.flask_config import FLASK_CONFIG_OBJECT

APP_CTX_CWD : os.PathLike = os.path.dirname(__file__)

def create_app() -> Flask:
    global APP_CTX_CWD
    app = Flask(import_name="RS",
                instance_path=os.path.join(APP_CTX_CWD, "instance"))
    app.config.from_object(FLASK_CONFIG_OBJECT)

    ### Database setup ###
    from resource_server.models import db, CONFIG
    db.init_app(app)
    migrate = Migrate(app, db)

### Test whether all entities specified in config.json under 'database' are present in the actual database instance ###
    public_tables = [item for entities in CONFIG['database']['entities'].values() for item in entities]
    with app.app_context():
        try:
            with db.engine.connect() as connection:
                tables = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")).fetchall()
                tables = list(map(lambda x : x[0], tables))
        except Exception as e:
            print(f"[{app.name}]: Error in processing database schema")
            print(f"[{app.name}]: Error Context\n===========================================\n{format_exc()}\n===========================================")
            print(f"[{app.name}] Exiting app factory...")
            exit(500)
                
        for table in public_tables:
            tables.remove(table)

        if tables:
            print(f"[{app.name}]: Mismatch in schema definition, tables {','.join(table for table in tables)} not specified in database configuration")
            print(f"[{app.name}] Exiting app factory...")
            exit(500)     

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