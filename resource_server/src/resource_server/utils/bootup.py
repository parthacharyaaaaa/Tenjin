from contextlib import asynccontextmanager
from typing import AsyncGenerator, Final

from fastapi import APIRouter, FastAPI

from auxillary.utils import generic_error_handler

from resource_server.blueprints.url_prefixes import URLPrefix
from resource_server.dependencies import get_key_manager
from resource_server.key_manager import KeyManager


def register_routers(
    app: FastAPI,
    api_prefixes: tuple[tuple[APIRouter, tuple[URLPrefix, ...]], ...],
    common_prefix: str = "",
) -> None:
    for router, url_prefixes in api_prefixes:
        app.include_router(
            router, prefix="/".join((common_prefix, *[u.value for u in url_prefixes]))
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.add_exception_handler(Exception, generic_error_handler)
    # TODO: Add route registration

    key_manager: Final[KeyManager] = get_key_manager()
    key_manager.current_mapping = await key_manager.get_global_key_mapping()
    if not key_manager.current_mapping:
        await key_manager.update_jwks()

    key_manager.begin_polling()

    yield

    await key_manager.stop_polling()
