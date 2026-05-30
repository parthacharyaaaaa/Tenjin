from typing import Final

from fastapi import FastAPI

from auth_server.utils.bootup import lifespan

app: Final[FastAPI] = FastAPI(lifespan=lifespan)
