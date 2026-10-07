from collections.abc import Callable
from typing import Any

import pytest
from auth_server.admin.roles import AdminRole

type PayloadFactory = Callable[..., dict[str, Any]]


@pytest.fixture
def authentication_payload() -> PayloadFactory:
    def make_payload(**overrides: Any) -> dict[str, Any]:
        return {
            "identity": "user_123",
            "password": "password123",
        } | overrides

    return make_payload


@pytest.fixture
def registration_payload() -> PayloadFactory:
    def make_payload(**overrides: Any) -> dict[str, Any]:
        return {
            "identity": "user_123",
            "email": "user@example.com",
            "password": "password123",
        } | overrides

    return make_payload


@pytest.fixture
def admin_session_payload() -> PayloadFactory:
    def make_payload(**overrides: Any) -> dict[str, Any]:
        return {
            "session_id": "session-id",
            "admin_id": 7,
            "expiry_timestamp": 200,
            "revival_digest": "revival-digest",
            "epoch_timestamp": 100,
            "role": AdminRole.STAFF,
            "iteration": 1,
        } | overrides

    return make_payload
