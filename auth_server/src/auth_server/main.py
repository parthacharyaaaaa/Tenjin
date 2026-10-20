from pathlib import Path
from typing import Final

# TEMP LOGIC
from dotenv import load_dotenv
from fastapi import FastAPI
from resource_server.depedencies.local import get_app_config

from auth_server.config import AppConfig
from auth_server.routers import ROUTER_URL_MAPPING
from auth_server.utils.bootup import lifespan, register_routers

env_path: Path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(str(env_path))

app: Final[FastAPI] = FastAPI(lifespan=lifespan)
app_config: Final[AppConfig] = get_app_config()
register_routers(app, ROUTER_URL_MAPPING, app_config.CORE.APPLICATION_ROOT)
