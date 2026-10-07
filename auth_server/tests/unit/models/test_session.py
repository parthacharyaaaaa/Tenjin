from collections.abc import Callable
from typing import Any

import pytest
from auth_server.admin.roles import AdminRole
from auth_server.models.session import AdminSession
from pydantic import ValidationError

type PayloadFactory = Callable[..., dict[str, Any]]


@pytest.mark.parametrize(
    ("epoch_timestamp", "expiry_timestamp"),
    ((1, 2), (100, 200), (199, 200)),
)
def test_session_accepts_epoch_before_expiry(
    admin_session_payload: PayloadFactory,
    epoch_timestamp: int,
    expiry_timestamp: int,
) -> None:
    AdminSession(
        **admin_session_payload(
            epoch_timestamp=epoch_timestamp,
            expiry_timestamp=expiry_timestamp,
        )
    )


@pytest.mark.parametrize(
    ("epoch_timestamp", "expiry_timestamp"),
    ((1, 1), (2, 1), (201, 200)),
)
def test_session_rejects_epoch_at_or_after_expiry(
    admin_session_payload: PayloadFactory,
    epoch_timestamp: int,
    expiry_timestamp: int,
) -> None:
    with pytest.raises(
        ValidationError,
        match="must be lesser than session expiry",
    ):
        AdminSession(
            **admin_session_payload(
                epoch_timestamp=epoch_timestamp,
                expiry_timestamp=expiry_timestamp,
            )
        )


@pytest.mark.parametrize("role", tuple(AdminRole))
def test_session_dump_converts_role_for_redis(
    admin_session_payload: PayloadFactory,
    role: AdminRole,
) -> None:
    session = AdminSession(**admin_session_payload(role=role))

    assert session.model_dump_redis() == {
        "session_id": "session-id",
        "admin_id": 7,
        "expiry_timestamp": 200,
        "revival_digest": "revival-digest",
        "epoch_timestamp": 100,
        "role": role.value,
        "iteration": 1,
    }


@pytest.mark.parametrize(
    ("mapping", "expected"),
    (
        ({}, True),
        ({"key": "value"}, True),
        ({b"key": 1}, True),
        ({memoryview(b"key"): 1.5}, True),
        ({1: "value"}, False),
        ({"key": b"value"}, False),
        ({"key": None}, False),
    ),
)
def test_redis_mapping_validation(
    mapping: dict[Any, Any],
    expected: bool,
) -> None:
    assert AdminSession._is_redis_dict(mapping) is expected


@pytest.mark.parametrize("role", tuple(AdminRole))
@pytest.mark.parametrize("iteration", (1, 2, 10))
def test_session_successor_replaces_rotated_values_and_preserves_identity(
    admin_session_payload: PayloadFactory,
    role: AdminRole,
    iteration: int,
) -> None:
    preceding = AdminSession(**admin_session_payload(role=role, iteration=iteration))

    successor = AdminSession.construct_session_successor(
        preceding,
        new_session_id="next-session-id",
        epoch_timestamp=300,
        expiry_timestamp=400,
        revival_digest="next-revival-digest",
    )

    assert successor.session_id == "next-session-id"
    assert successor.admin_id == preceding.admin_id
    assert successor.epoch_timestamp == 300
    assert successor.expiry_timestamp == 400
    assert successor.revival_digest == "next-revival-digest"
    assert successor.role is role
    assert successor.iteration == iteration + 1
