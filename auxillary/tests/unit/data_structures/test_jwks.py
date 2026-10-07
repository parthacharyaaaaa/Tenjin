import orjson
import pytest
from auxillary.security.data_structures.jwks.models import EllipticCurveJWK
from auxillary.utils import to_base64url
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.fixture
def deterministic_ec_jwk() -> EllipticCurveJWK:
    dummy_key = ec.derive_private_key(private_value=1, curve=ec.SECP256K1())
    public_numbers = dummy_key.public_key().public_numbers()
    return EllipticCurveJWK(
        kid="foo", x=to_base64url(public_numbers.x), y=to_base64url(public_numbers.y)
    )


def test_ec_orjson_serialization(deterministic_ec_jwk: EllipticCurveJWK) -> None:
    assert (
        deterministic_ec_jwk.model_dump()
        == EllipticCurveJWK.model_validate(
            orjson.loads(orjson.dumps(deterministic_ec_jwk.model_dump()))
        ).model_dump()
    )
