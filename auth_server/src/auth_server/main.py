from pathlib import Path
from typing import Final

# TEMP LOGIC
from dotenv import load_dotenv
from fastapi import FastAPI

from auth_server.utils.bootup import lifespan

env_path: Path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(str(env_path))

app: Final[FastAPI] = FastAPI(lifespan=lifespan)
