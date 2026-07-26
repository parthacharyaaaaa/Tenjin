from typing import Final

from fastapi import FastAPI

from auth_server.utils.bootup import lifespan

# TEMP LOGIC
from dotenv import load_dotenv
from pathlib import Path

env_path: Path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(str(env_path))

app: Final[FastAPI] = FastAPI(lifespan=lifespan)
