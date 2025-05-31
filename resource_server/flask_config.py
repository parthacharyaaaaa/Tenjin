import os
from dotenv import load_dotenv
from traceback import format_exc
from datetime import timedelta
bLoaded : bool = load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"),
                             verbose=True, override=True)
if not bLoaded:
    print(f"Failed to load .env file for resource server, please ensure .env exists within this directory: {os.path.dirname(__file__)}")
    raise FileNotFoundError

class FlaskConfig:
    try:
        ### Flask Configurations ###
        APP_PORT: int = int(os.environ["FLASK_PORT"])
        APP_HOST: str = os.environ["FLASK_HOST"]
        APP_DEBUG: bool = bool(int(os.environ.get("FLASK_DEBUG", 0)))
        SECRET_KEY: str = os.environ["FLASK_SECRET_KEY"]
        KEY_VK_MAPPING: dict[str, bytes] = {}

        # TODO: Add JWT signing key, peer server metadata, and Redis data. (Maybe session configs too if we use a hybrid approach instead of pure REST?)

        ### Database Configurations ###
        SQLALCHEMY_DATABASE_URI : str = "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}".format(username=os.environ["POSTGRES_USERNAME"],
                                                                                                                    password=os.environ["POSTGRES_PASSWORD"],
                                                                                                                    host=os.environ["POSTGRES_HOST"],
                                                                                                                    port=os.environ.get("POSTGRES_PORT", 5432),
                                                                                                                    database=os.environ["POSTGRES_DATABASE"])
        SQLALCHEMY_POOL_SIZE = int(os.environ.get("SQLALCHEMY_POOL_SIZE", 10))
        SQLALCHEMY_MAX_OVERFLOW = int(os.environ.get("SQLALCHEMY_MAX_OVERFLOW", 5))
        SQLALCHEMY_POOL_RECYCLE = int(os.environ.get("SQLALCHEMY_POOL_RECYCLE", 600))
        SQLALCHEMY_POOL_TIMEOUT = int(os.environ.get("SQLALCHEMY_POOL_TIMEOUT", 30))
        SQLALCHEMY_TRACK_MODIFICATIONS = bool(int(os.environ.get("SQLALCHEMY_TRACK_MODIFICATIONS", 0)))

        ### JWT ###
        AUTH_SERVER_URL: str = f'{os.environ["AUTH_SERVER_PROTOCOL"]}://{os.environ["AUTH_SERVER_HOSTNAME"]}:{os.environ["AUTH_SERVER_PORT"]}'

        ### Application-specific configurations ###
        ACCOUNT_RECOVERY_PERIOD: timedelta = timedelta(days=int(os.environ["ACCOUNT_RECOVERY_PERIOD"]))
        PASSWORD_TOKEN_MAX_AGE: timedelta = timedelta(minutes=int(os.environ['PASSWORD_TOKEN_MAX_AGE']))

        ### Redis Configuration ###
        REDIS_HOST: str = os.environ["REDIS_HOST"]
        REDIS_PORT: int = int(os.environ["REDIS_PORT"])
        REDIS_DB: int = int(os.environ.get("REDIS_DB", 0))
        REDIS_TTL_CAP: int = int(os.environ["TTL_CAP"])
        REDIS_TTL_PROMOTION: int = int(os.environ["TTL_PROMOTION"])
        REDIS_TTL_STRONG: int = int(os.environ["TTL_STRONG"])
        REDIS_TTL_WEAK: int = int(os.environ["TTL_WEAK"])
        REDIS_TTL_EPHEMERAL: int = int(os.environ["TTL_EPHEMERAL"])
        ANNOUNCEMENT_DURATION: int = int(os.environ.get('ANNOUNCEMENT_DURATION', 300))
        JWKS_POLL_COOLDOWN: int = int(os.environ.get('JWKS_POLL_COOLDOWN', 600))

        GENERIC_HTTP_MESSAGES : dict = {2 : "Success",
                                3 : "Redirection",
                                4 : "Client-Side Error",
                                5 : "There seems to be an issue at our server. Please try again later"}

    except (TypeError, ValueError):
        print("\n\nERROR: Invalid format/type for environment variables\n\n")
        print(format_exc())
        raise Exception()
    except KeyError:
        print("\n\n ERROR: Failed to load environment variables\n\n")
        print(format_exc())
        raise KeyError()


FLASK_CONFIG_OBJECT = FlaskConfig()