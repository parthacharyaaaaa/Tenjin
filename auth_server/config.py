import os
from dotenv import load_dotenv
import json
from typing import Any

CWD = os.path.dirname(__file__)
bLoaded: bool = load_dotenv(os.path.join(CWD, "auth.env"), override=True)
if not bLoaded:
    raise FileNotFoundError("Auth server requires env vars to be loaded")

class FlaskConfig:
    try:
        # Security metadata
        SECRET_KEY: str = os.environ["SECRET_KEY"]
        PRIVATE_PEM_ENCRYPTION_KEY: bytes = bytes(os.environ["PEM_ENCRYPTION_KEY"], encoding='utf-8')
        SIGNING_KEY: str = os.environ["SIGNING_KEY"]
        JWKS_FILENAME: str = os.environ["JWKS_FILENAME"]
        JWKS_CAP: int = int(os.environ['JWKS_CAP'])
        JWKS_KV_MAPPING: dict = None
        SESSION_COOKIE_SECURE: bool = bool(os.environ["SESSION_COOKIE_SECURE"])
        PRIVATE_COMM_KEYS: list = os.environ["PRIVATE_COMM_KEYS"].split(",")
        CSP: str = f"default-src 'self'; connect-src 'self' {os.environ['RS_DOMAIN']}"
        
        # IP metadata
        VALID_PROXIES : list = os.environ["VALID_PROXIES"].split(",")
        PRIVATE_IP_ADDRS : list = os.environ["PRIVATE_COMM_IP"].split(",")

        # Addressing metadata
        PORT: int = int(os.environ["PORT"])
        HOST: str = os.environ["HOST"]

        # Resource server metadata
        RESOURCE_SERVER_ORIGIN = os.environ["RS_DOMAIN"].lower()
        PROTOCOL = os.environ.get("RS_COMMUNICATION_PROTOCOL", "http").lower()

        # Redis metadata
        RELATIVE_PATH: os.PathLike = os.environ['REDIS_CONFIG_REL_FPATH']
        with open(os.path.join(CWD, RELATIVE_PATH)) as configFile:
            REDIS_KWARGS: dict[str, Any] = json.loads(configFile.read())

    except KeyError as e:
        raise ValueError(f"FAILED TO SETUP CONFIGURATIONS FOR FLASK AUTH APPLICATION AS ENVIRONMENT VARIABLES WERE NOT FOUND (SEE: class Flask_Config at '{__file__}')")
    except TypeError as e:
        raise TypeError(f"FAILURE IN CONFIGURING ENVIRONMENT VARIABLE(S) OF TYPE: INT (SEE: class Flask_Config at '{__file__}')")
    
flaskconfig = FlaskConfig()