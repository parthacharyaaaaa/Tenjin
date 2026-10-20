from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from auth_server.admin.roles import AdminRole
from auth_server.dependencies import local
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.keydata import KeydataRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.routers import EXCEPTION_HANDLER_MAPPING, ROUTER_URL_MAPPING
from auth_server.strings import AdminStrings
from auth_server.subsystems.jwks_announcer import JWKSUpdateAnnouncer
from auth_server.subsystems.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.subsystems.session_manager import AdminSessionManager
from auth_server.subsystems.token_manager import TokenManager
from auth_server.utils.bootup import register_exception_handler, register_routers
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from fastapi.applications import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from redis.asyncio.client import Redis


@pytest.fixture
def auth_app(
    token_manager: TokenManager,
    admin_repository: AdminRepository,
    keydata_repository: KeydataRepository,
    suspicious_activity_repository: SuspiciousActivityRepository,
    key_lifecycle_manager: KeyLifecycleManager,
    jwks_update_announcer: JWKSUpdateAnnouncer,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    admin_session_manager: AdminSessionManager,
    redis_lock_factory: RedisInstanceLockFactory,
    token_store_client: Redis,
    async_redis_client: Redis,
) -> Generator[FastAPI, None, None]:
    app = FastAPI()
    register_exception_handler(app, EXCEPTION_HANDLER_MAPPING)
    # Omitting application root path, i.e. "/api/v*"
    register_routers(app, ROUTER_URL_MAPPING)
    app.dependency_overrides.update(  # type: ignore
        {
            local.get_token_manager: lambda: token_manager,
            local.get_admin_repository: lambda: admin_repository,
            local.get_keydata_repository: lambda: keydata_repository,
            local.get_suspicious_activity_repository: lambda: (
                suspicious_activity_repository
            ),
            local.get_key_lifecycle_manager: lambda: key_lifecycle_manager,
            local.get_jwks_update_announcer: lambda: jwks_update_announcer,
            local.get_synced_store_key_state_manager: lambda: synced_key_store_manager,
            local.get_admin_session_manager: lambda: admin_session_manager,
            local.get_distributed_lock_factory: lambda: redis_lock_factory,
            local.get_token_store_client: lambda: token_store_client,
            local.get_synced_store_client: lambda: async_redis_client,
        }
    )
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_client(auth_app: FastAPI):
    async with LifespanManager(auth_app):
        async with AsyncClient(
            transport=ASGITransport(app=auth_app), base_url="http://testserver"
        ) as client:
            yield client


@pytest_asyncio.fixture
async def super_admin_test_client(
    test_client: TestClient, admin_session_manager: AdminSessionManager
) -> AsyncGenerator[TestClient, None]:
    session_id, revival_digest = await admin_session_manager.initialize_session(
        1, AdminRole.SUPER
    )
    try:
        test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})
        yield test_client
    finally:
        await admin_session_manager.terminate_session(session_id, 1)
