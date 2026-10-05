from typing import Final, get_args

import pytest
from auxillary.security import hashing

bcrypt_salt_prefix_test_data: Final[tuple[hashing.BCRYPT_SALT_PREFIXES, ...]] = tuple(
    prefix for prefix in get_args(hashing.BCRYPT_SALT_PREFIXES)
)


@pytest.mark.slow
@pytest.mark.parametrize("salt_prefix", bcrypt_salt_prefix_test_data)
def test_bcrypt_hashing(salt_prefix: hashing.BCRYPT_SALT_PREFIXES) -> None:
    plaintext_password: str = "supersecretpassword!"
    hashed_pw = hashing.bcrypt_hash_password(
        plaintext_password, salt_prefix=salt_prefix
    )
    assert hashing.bcrypt_check_password(plaintext_password, hashed_pw), (
        f"Bcrypt hash failed for salt prefix: {salt_prefix}"
    )
