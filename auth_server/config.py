import os
from dotenv import load_dotenv
import json
from typing import Any
from datetime import timedelta

CWD = os.path.dirname(__file__)
bLoaded: bool = load_dotenv(os.path.join(CWD, "auth.env"), override=True)
if not bLoaded:
    raise FileNotFoundError("Auth server requires env vars to be loaded")

class FlaskConfig:
    try:
        # Security metadata
        APPLICATION_ROOT: str = os.environ['APPLICAITON_ROOT']
        SECRET_KEY: str = os.environ["SECRET_KEY"]
        PRIVATE_PEM_ENCRYPTION_KEY: bytes = bytes(os.environ["PEM_ENCRYPTION_KEY"], encoding='utf-8')
        SIGNING_KEY: str = os.environ["SIGNING_KEY"]
        JWKS_FILENAME: str = os.environ["JWKS_FILENAME"]
        JWKS_CAP: int = int(os.environ['JWKS_CAP'])
        JWKS_KV_MAPPING: dict = None
        SESSION_COOKIE_SECURE: bool = bool(os.environ["SESSION_COOKIE_SECURE"])
        CSP: str = f"default-src 'self'; connect-src 'self' {os.environ['RS_DOMAIN']}"
        
        ### Database Configurations ###
        SQLALCHEMY_DATABASE_URI : str = "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}".format(username=os.environ["AUTH_WORKER_POSTGRES_USERNAME"],
                                                                                                                      password=os.environ["AUTH_WORKER_POSTGRES_PASSWORD"],
                                                                                                                      host=os.environ["POSTGRES_HOST"],
                                                                                                                      port=os.environ.get("POSTGRES_PORT", 5432),
                                                                                                                      database=os.environ["POSTGRES_DATABASE"])
        SQLALCHEMY_POOL_SIZE = int(os.environ.get("SQLALCHEMY_POOL_SIZE", 10))
        SQLALCHEMY_MAX_OVERFLOW = int(os.environ.get("SQLALCHEMY_MAX_OVERFLOW", 5))
        SQLALCHEMY_POOL_RECYCLE = int(os.environ.get("SQLALCHEMY_POOL_RECYCLE", 600))
        SQLALCHEMY_POOL_TIMEOUT = int(os.environ.get("SQLALCHEMY_POOL_TIMEOUT", 30))
        SQLALCHEMY_TRACK_MODIFICATIONS = bool(int(os.environ.get("SQLALCHEMY_TRACK_MODIFICATIONS", 0)))

        # Addressing metadata
        PORT: int = int(os.environ["PORT"])
        HOST: str = os.environ["HOST"]

        # Resource server metadata
        RESOURCE_SERVER_ORIGIN = os.environ["RS_DOMAIN"].lower()
        RESOURCE_SERVER_URL_PREFIX = os.environ['RS_URL_PREFIX'].lower()
        PROTOCOL = os.environ.get("RS_COMMUNICATION_PROTOCOL", "http").lower()

        # Business logic
        SUSPICIOUS_LOOKBACK_TIME: timedelta = timedelta(days=int(os.environ['SUSPICIOUS_LOOKBACK_TIME']))
        MAX_ACTIVITY_LIMIT: int = int(os.environ['MAX_ACTIVITY_LIMIT'])
        MAX_SESSION_ITERATIONS: int = int(os.environ.get('MAX_SESSION_ITERATIONS', 3))
        ANNOUNCEMENT_DURATION: int = int(os.environ.get('ANNOUNCEMENT_DURATION', 900)) # Duration in seconds
        ADMIN_SESSION_DURATION: int = int(os.environ['ADMIN_SESSION_DURATION']) # Duration in seconds
        MAX_VALID_KEYS: int = int(os.environ['MAX_VALID_KEYS'])
        KEY_ROTATION_COOLDOWN: int = int(os.environ.get('KEY_ROTATION_COOLDOWN', 60*60*24)) # Default to 1 day in seconds

    except KeyError as e:
        raise ValueError(f"FAILED TO SETUP CONFIGURATIONS FOR FLASK AUTH APPLICATION AS ENVIRONMENT VARIABLES WERE NOT FOUND (SEE: class Flask_Config at '{__file__}')")
    except TypeError as e:
        raise TypeError(f"FAILURE IN CONFIGURING ENVIRONMENT VARIABLE(S) OF TYPE: INT (SEE: class Flask_Config at '{__file__}')")
    
flaskconfig = FlaskConfig()