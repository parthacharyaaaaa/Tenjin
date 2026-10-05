from typing import Final

import pytest
from auxillary.utils import from_base64url, to_base64url
from cryptography.hazmat.primitives.asymmetric import ec

P256_PUBLIC_NUMBERS: Final[ec.EllipticCurvePublicNumbers] = (
    ec.derive_private_key(
        1,
        ec.SECP256R1(),
    )
    .public_key()
    .public_numbers()
)

BASE64URL_INTEGERS: Final[tuple[int, ...]] = (
    0,
    1,
    255,
    256,
    2**128,
    2**255,
    2**256 - 1,
    P256_PUBLIC_NUMBERS.x,
    P256_PUBLIC_NUMBERS.y,
)


@pytest.mark.parametrize("integer", BASE64URL_INTEGERS)
def test_point_base64_serialization(integer: int) -> None:
    assert integer == from_base64url(to_base64url(integer))


@pytest.mark.parametrize("integer", (-1, 2**256))
def test_base64url_rejects_values_outside_default_width(integer: int) -> None:
    with pytest.raises(OverflowError):
        to_base64url(integer)
