from typing import Literal

import bcrypt


def bcrypt_hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    salt_generation_rounds: int = 12,
    salt_prefix: Literal[b"2", b"2a", b"2x", b"2y", b"2b"] = b"2b",
    password_codec: str = "utf-8",
) -> bytes:
    if not salt:
        salt = bcrypt.gensalt(salt_generation_rounds, salt_prefix)
    return bcrypt.hashpw(password.encode(password_codec), salt)


def bcrypt_check_password(
    password: str, password_hash: bytes, *, password_codec: str = "utf-8"
) -> bool:
    return bcrypt.checkpw(password.encode(password_codec), password_hash)
