from collections.abc import Callable
from typing import Any

import pytest
from auth_server.models.auth_requests import AuthenticationModel
from auth_server.models.cmd_requests import AdminAuthenticationModel
from pydantic import ValidationError

type PayloadFactory = Callable[..., dict[str, Any]]


def test_admin_authentication_uses_username_only_identity_stream(
    authentication_payload: PayloadFactory,
) -> None:
    payload = authentication_payload(identity="admin@example.com")

    assert AuthenticationModel(**payload).identity == "admin@example.com"
    with pytest.raises(ValidationError):
        AdminAuthenticationModel(**payload)


def test_admin_authentication_normalizes_username_and_password(
    authentication_payload: PayloadFactory,
) -> None:
    model = AdminAuthenticationModel(
        **authentication_payload(
            identity="  admin_user  ",
            password="  password123  ",
        )
    )

    assert model.identity == "admin_user"
    assert model.password == "password123"
