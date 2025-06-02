import os
from flask import Flask
from flask.cli import with_appcontext
from flask_migrate import Migrate
from traceback import format_exc
from resource_server.flask_config import FLASK_CONFIG_OBJECT
from auxillary.utils import generic_error_handler
from resource_server.resource_auxillary import background_poll
from types import MappingProxyType

APP_CTX_CWD : os.PathLike = os.path.dirname(__file__)
def create_app() -> Flask:
    global APP_CTX_CWD
    app = Flask(import_name="RS",
                instance_path=os.path.join(APP_CTX_CWD, "instance"),
                static_folder=os.path.join(APP_CTX_CWD, 'static'))
    
    app.config.from_object(FLASK_CONFIG_OBJECT)
    app.cli.name = "RS"
    app.register_error_handler(Exception, generic_error_handler)

    ### Database setup ###
    from resource_server.models import db, CONFIG
    db.init_app(app)
    migrate = Migrate(app, db)

    ### External Extensions ###
    from resource_server.external_extensions import init_redis
    init_redis(app)

    ### Blueprints registaration ###
    from resource_server.blueprint_forum import forum
    from resource_server.blueprint_user import user
    from resource_server.blueprint_posts import post
    from resource_server.blueprint_animes import anime
    from resource_server.blueprint_templates import templates
    from resource_server.blueprint_misc import misc
    app.register_blueprint(forum)
    app.register_blueprint(user)
    app.register_blueprint(post)
    app.register_blueprint(templates)
    app.register_blueprint(anime)
    app.register_blueprint(misc)

    ### Additional commands ###
    def fetch_genres(db: SQLAlchemy) -> None:
        with app.app_context():
            with db.engine.connect() as conn:
                GENRES: tuple[str] = conn.execute(text('SELECT _name, id FROM genres;')).fetchall()

                app.config['GENRES'] = MappingProxyType({genre[0] : genre[1] for genre in GENRES})

    ### Additional CLI commands ###
   # Instantiate the database
    @app.cli.command("make_db")
    @with_appcontext
    def make_db() -> None:
        query = text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        with db.engine.connect() as conn:
            tables = conn.execute(query).fetchall()

            if tables:
                genesis_prompt = input(f"[{app.name}]: Database already populated, proceed with db creation (y/n)?\n").lower()
                if genesis_prompt == "n":
                    print(f"[{app.name}]: Exiting...")
                    exit(0)
                elif genesis_prompt != "y":
                    print(f"[{app.name}]: Invalid input to prompt, exiting...")
                    exit(500)

            tables : set = set(map(lambda x : x[0], tables))
            db.create_all()
            print(f"[{app.name}]: Creating database{' again...' if tables else '...'}")
            try:
                new_tables : set = set(map(lambda x : x[0], conn.execute(query)))
                insertion_difference : set = new_tables - tables
                print(f"[{app.name}]: Tables Created: {', '.join(list(insertion_difference)) or 'None. You just wasted your time.'}")
            except:
                print(format_exc())
                print(f"[{app.name}]: Failed database operation")
                exit(500)

    # Test whether all entities specified in config.json under 'database' are present in the actual database instance
    @app.cli.command("validate_db")
    @with_appcontext
    def validate_db():
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

            try:   
                for table in public_tables:
                    tables.remove(table)
            except ValueError:
                print(f"[{app.name}]: Mismatch in schema definition, table '{table}' specified in database configuration but not found in database")
                print(f"[{app.name}] Exiting app factory...")
                exit(500)


            if tables:
                print(f"[{app.name}]: Mismatch in schema definition, tables {','.join(table for table in tables)} not specified in database configuration but found in database")
                print(f"[{app.name}] Exiting app factory...")
                exit(500)     

    fetch_genres(db)
    # from resource_server.external_extensions import RedisInterface
    # background_poll(current_app=app, RedisInterface=RedisInterface)
    from resource_server.external_extensions import RedisInterface
    from resource_server.resource_auxillary import update_jwks

    # Initial JWKS load
    jwks_mapping: dict[str, str] = RedisInterface.hgetall('JWKS_MAPPING')
    if not jwks_mapping:
        # updaet_jwks() already handles race conditions among multiple workers trying to update JWKS mapping, so no need to have separate logic here
        jwks_mapping = update_jwks(endpoint=f"{app.config['AUTH_SERVER_URL']}/auth/jwks.json", currentMapping={}, RedisInterface=RedisInterface, current_app=app)
        if not jwks_mapping:
            raise RuntimeError("Failed to initialize JWKS mapping in Redis")
    app.config['KEY_VK_MAPPING'] = jwks_mapping
    return app

from resource_server.models import *
