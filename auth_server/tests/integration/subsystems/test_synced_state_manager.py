from typing import Final

import pytest
import pytest_asyncio
from auth_server.repositories.keydata import EllipticCurveJWKResult
from auth_server.strings import SyncedStoreKeyStrings
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auxillary.security.data_structures.jwks.enums import ECAlg
from auxillary.security.data_structures.jwks.models import EllipticCurveJWK
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, derive_private_key
from redis.asyncio.client import Redis


@pytest.fixture(scope="module")
def dummy_elliptic_jwks() -> list[EllipticCurveJWKResult]:
    return [
        EllipticCurveJWKResult(
            alg=ECAlg.ES256,
            crv=SECP256R1.name,
            kid=str(i),
            x=EllipticCurveJWK.b64url_encode_point(
                (
                    pub_nums := derive_private_key(private_value=i, curve=SECP256R1())
                    .public_key()
                    .public_numbers()
                ).x
            ),
            y=EllipticCurveJWK.b64url_encode_point(pub_nums.y),
        )
        for i in range(1, 4)
    ]


@pytest_asyncio.fixture(autouse=True)
async def prep_synced_store(clean_synced_store: None) -> None:
    return


@pytest.mark.asyncio
async def test_jwks_serialization(
    synced_key_store_manager: SyncedStoreKeyStateManager,
    dummy_elliptic_jwks: list[EllipticCurveJWKResult],
) -> None:
    await synced_key_store_manager.set_jwks(dummy_elliptic_jwks)
    result = await synced_key_store_manager.get_jwks()
    assert result == dummy_elliptic_jwks


@pytest.mark.asyncio
async def test_transactional_block(
    synced_key_store_manager: SyncedStoreKeyStateManager,
    dummy_elliptic_jwks: list[EllipticCurveJWKResult],
    async_redis_client: Redis,
) -> None:
    await synced_key_store_manager.set_jwks(dummy_elliptic_jwks)
    raw_jwks: Final[str] = await async_redis_client.get(SyncedStoreKeyStrings.JWKS_KEY)
    with pytest.raises(Exception, match="foo"):
        async with synced_key_store_manager.transactional_block():
            await synced_key_store_manager.set_jwks(dummy_elliptic_jwks[1:])
            raise Exception("foo")
    assert await async_redis_client.get(SyncedStoreKeyStrings.JWKS_KEY) == raw_jwks
