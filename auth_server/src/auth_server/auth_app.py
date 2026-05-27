import os
from typing import Final

from flask import Flask

from redis import Redis

from auth_server.repositories.keydata import KeydataRepository
from auth_server.strings import SyncedStoreStrings
from auxillary.utils import generic_error_handler

from auth_server.blueprints import BLUEPRINT_URL_MAPPING
from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_app_config,
    get_synced_store_client,
    get_database_session_maker,
)
from auth_server.utils import bootup


def create_app() -> Flask:
    auth_app = Flask(
        "auth_app",
        instance_path=os.path.join(os.getcwd(), "instance"),
        static_folder=os.path.join(os.getcwd(), "static"),
    )

    PID: Final[int] = os.getpid()

    config: Final[AppConfig] = get_app_config()

    # Additional filepaths depending on instance/static directories
    config.JWKS.resolve_jwks_directory(config.CORE.instance_path)
    config.JWKS.resolve_public_pem_directory(config.CORE.static_path)
    config.JWKS.resolve_private_pem_directory(config.CORE.instance_path)

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    synced_store_client: Final[Redis] = get_synced_store_client()

    keydata_repository: Final[KeydataRepository] = KeydataRepository(
        get_database_session_maker()
    )

    is_master: bool = bool(
        synced_store_client.set(SyncedStoreStrings.AUTH_BOOTUP_MASTER, PID, nx=True)
    )

    if is_master:
        bootup.master_bootup(config, synced_store_client, keydata_repository, PID)
    else:
        bootup.slave_bootup(config, synced_store_client, keydata_repository, PID)

    # Blueprints
    bootup.register_blueprints(
        auth_app, BLUEPRINT_URL_MAPPING, common_prefix=config.CORE.APPLICATION_ROOT
    )

    return auth_app
