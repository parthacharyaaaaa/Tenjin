from contextlib import asynccontextmanager
from typing import AsyncGenerator, Final

from fastapi import FastAPI

from auxillary.utils import generic_error_handler

from resource_server.config.app_config import AppConfig
from resource_server.routers import ROUTER_PREFIXES, t_route_prefixes
from resource_server.dependencies import get_app_config, get_key_manager
from resource_server.key_manager import KeyManager


def register_routers(
    app: FastAPI,
    api_prefixes: t_route_prefixes,
    common_prefix: str = "",
) -> None:
    for router, url_prefixes in api_prefixes:
        app.include_router(
            router, prefix="/".join((common_prefix, *[u.value for u in url_prefixes]))
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.add_exception_handler(Exception, generic_error_handler)

    app_config: Final[AppConfig] = get_app_config()

    register_routers(
        app, ROUTER_PREFIXES, common_prefix=app_config.CORE.APPLICATION_ROOT
    )

    key_manager: Final[KeyManager] = get_key_manager()
    key_manager.current_mapping = await key_manager.get_global_key_mapping()
    if not key_manager.current_mapping:
        await key_manager.update_jwks()

    key_manager.start_jwks_monitoring()

    yield

    await key_manager.stop_jwks_monitoring()
