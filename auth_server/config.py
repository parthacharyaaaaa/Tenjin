import os
from dotenv import load_dotenv

CWD = os.path.dirname(__file__)
bLoaded: bool = load_dotenv(os.path.join(CWD, "auth.env"), override=True)
if not bLoaded:
    raise FileNotFoundError("Auth server requires env vars to be loaded")

class FlaskConfig:
    try:
        # Security metadata
        SECRET_KEY = os.environ["SECRET_KEY"]
        SIGNING_KEY = os.environ["SIGNING_KEY"]
        SESSION_COOKIE_SECURE = bool(os.environ["SESSION_COOKIE_SECURE"])
        PRIVATE_COMM_KEYS : list = os.environ["PRIVATE_COMM_KEYS"].split(",")
        CSP = f"default-src 'self'; connect-src 'self' {os.environ['RS_DOMAIN']}"
        
        # IP metadata
        VALID_PROXIES : list = os.environ["VALID_PROXIES"].split(",")
        PRIVATE_IP_ADDRS : list = os.environ["PRIVATE_COMM_IP"].split(",")

        # Addressing metadata
        PORT = int(os.environ["PORT"])
        HOST = os.environ["HOST"]

        # Resource server metadata
        RESOURCE_SERVER_ORIGIN = os.environ["RS_DOMAIN"].lower()
        PROTOCOL = os.environ.get("RS_COMMUNICATION_PROTOCOL", "http").lower()

        # Redis metadata
        REDIS_HOST: str = os.environ["REDIS_HOST"]
        REDIS_PORT: int = os.environ["REDIS_PORT"]
        REDIS_DB: int = os.environ.get("REDIS_DB", 0)

    except KeyError as e:
        raise ValueError(f"FAILED TO SETUP CONFIGURATIONS FOR FLASK AUTH APPLICATION AS ENVIRONMENT VARIABLES WERE NOT FOUND (SEE: class Flask_Config at '{__file__}')")
    except TypeError as e:
        raise TypeError(f"FAILURE IN CONFIGURING ENVIRONMENT VARIABLE(S) OF TYPE: INT (SEE: class Flask_Config at '{__file__}')")
    
flaskconfig = FlaskConfig()