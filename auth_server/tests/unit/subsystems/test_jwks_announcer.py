from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import orjson
import pytest
from auth_server.subsystems.jwks_announcer import JWKSUpdateAnnouncer
from auxillary.security.data_structures.jwks.models import (
    EllipticCurveJWK,
    EllipticCurveJWKS,
)
from cryptography.hazmat.primitives.asymmetric import ec


@dataclass
class RedisHarness:
    calls = Mock


@pytest.fixture
def deterministic_ec_jwks() -> EllipticCurveJWKS:
    keys_list: list[EllipticCurveJWK] = []
    for private_value in range(1, 3):
        dummy_key = ec.derive_private_key(
            private_value=private_value, curve=ec.SECP256R1()
        )
        public_numbers = dummy_key.public_key().public_numbers()
        keys_list.append(
            EllipticCurveJWK(
                kid=str(private_value),
                x=EllipticCurveJWK.b64url_encode_point(public_numbers.x),
                y=EllipticCurveJWK.b64url_encode_point(public_numbers.y),
            )
        )
    return EllipticCurveJWKS.model_validate({"keys": keys_list})


@pytest.mark.asyncio
async def test_ec_jwks_updation_message(
    deterministic_ec_jwks: EllipticCurveJWKS,
) -> None:
    stream: list[tuple[str, dict[str, bytes]]] = []

    async def update_stream(stream_name, fields: dict[str, bytes]) -> None:
        nonlocal stream
        stream.append((stream_name, fields))

    mock_redis = AsyncMock()
    mock_redis.xadd = update_stream
    jwks_announcer: JWKSUpdateAnnouncer = JWKSUpdateAnnouncer(
        mock_redis,
        SimpleNamespace(UPDATION_STREAM_NAME="foo"),  # pyrefly: ignore[bad-argument-type]
    )
    await jwks_announcer.stream_update(deterministic_ec_jwks)

    assert (
        deterministic_ec_jwks.model_dump()
        == EllipticCurveJWKS.model_validate(
            orjson.loads(stream[0][1]["jwks"])
        ).model_dump()
    ), "Serialized ec jwks does not match"
