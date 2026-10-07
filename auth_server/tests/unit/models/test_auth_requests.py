from collections.abc import Callable
from typing import Any

import pytest
from auth_server.models.auth_requests import AuthenticationModel, RegistrationModel
from pydantic import ValidationError

type PayloadFactory = Callable[..., dict[str, Any]]


@pytest.mark.parametrize(
    ("identity", "expected"),
    (
        ("  user_123  ", "user_123"),
        ("  user@example.com  ", "user@example.com"),
    ),
)
def test_authentication_normalizes_supported_identity_streams(
    authentication_payload: PayloadFactory,
    identity: str,
    expected: str,
) -> None:
    model = AuthenticationModel(**authentication_payload(identity=identity))

    assert model.identity == expected


@pytest.mark.parametrize(
    "identity",
    (
        "user name",
        "user!",
        "user@example",
        "@example.com",
    ),
)
def test_authentication_rejects_identity_matching_neither_stream(
    authentication_payload: PayloadFactory,
    identity: str,
) -> None:
    with pytest.raises(ValidationError):
        AuthenticationModel(**authentication_payload(identity=identity))


def test_authentication_normalizes_password_before_validation(
    authentication_payload: PayloadFactory,
) -> None:
    model = AuthenticationModel(**authentication_payload(password="  password123  "))

    assert model.password == "password123"


def test_registration_normalizes_each_credential(
    registration_payload: PayloadFactory,
) -> None:
    model = RegistrationModel(
        **registration_payload(
            identity="  user_123  ",
            email="  user@example.com  ",
            password="  password123  ",
        )
    )

    assert model.identity == "user_123"
    assert model.email == "user@example.com"
    assert model.password == "password123"


@pytest.mark.parametrize(
    ("overrides", "invalid_field"),
    (
        ({"identity": "user@example.com"}, "identity"),
        ({"email": "user_123"}, "email"),
        ({"identity": "user name"}, "identity"),
        ({"email": "user@example"}, "email"),
    ),
)
def test_registration_keeps_username_and_email_streams_distinct(
    registration_payload: PayloadFactory,
    overrides: dict[str, str],
    invalid_field: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RegistrationModel(**registration_payload(**overrides))

    assert exc_info.value.errors()[0]["loc"] == (invalid_field,)
