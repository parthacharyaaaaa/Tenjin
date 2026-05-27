from pathlib import Path
from typing import Sequence

import ecdsa
from hashlib import sha512
import os
import secrets
from auth_server.key_container import KeyMetadata
from auth_server.repositories.keydata import KeydataRepository
from auxillary.utils import to_base64url
import ujson

from auth_server.models.database import KeyData


def generate_ecdsa_pair() -> tuple[str, ecdsa.SigningKey, ecdsa.VerifyingKey]:
    """Generate signing and verification ECDSA key pair"""
    signing_key: ecdsa.SigningKey = ecdsa.SigningKey.generate(
        curve=ecdsa.SECP256k1, hashfunc=sha512
    )

    # ecdsa.SigningKey.get_verifying_key() is typed to return None,
    # but actually returns ecdsa.VerifyingKey :/
    verify_key: ecdsa.VerifyingKey = signing_key.get_verifying_key()  # type: ignore[reportAssignmentType]
    kid: str = str(secrets.randbelow(10_000_000))

    return kid, signing_key, verify_key


def initialize_jwks(jwks_filepath: Path, keys: Sequence[KeyData]) -> None:
    jwks_contents: list[dict[str, str | int]] = []
    for key in keys:
        # ecdsa.VerifyingKey.pubkey is hinted as being None thanks to its constructor
        # but actually does return a valid type
        point = ecdsa.VerifyingKey.from_pem(key.public_pem).pubkey.point  # type: ignore[reportAttributeAccessIssue]
        jwks_contents.append(
            {
                "kty": "EC",
                "alg": key.alg,
                "crv": key.curve,
                "use": "sig",
                "kid": key.kid,
                "x": to_base64url(int(point.x())),
                "y": to_base64url(int(point.y())),
            }
        )

        jwks_filepath.write_bytes(
            ujson.dumps({"keys": jwks_contents}, indent=2, ensure_ascii=True).encode(
                "utf-8"
            )
        )


def update_jwks(
    vk: ecdsa.VerifyingKey,
    kid: str,
    jwks_json_filepath: os.PathLike,
    enforce_capacity: bool = True,
    capacity: int = 3,
) -> None:
    """Updates the JWKS JSON file to include the given public key as the latest key"""
    # ecdsa.VerifyingKey.pubkey is hinted as being None thanks to its constructor
    # but actually does return a valid type
    point = vk.pubkey.point  # type: ignore[reportAttributeAccessIssue]
    encodedX, encodedY = to_base64url(int(point.x())), to_base64url(int(point.y()))
    keyMapping: dict[str, str | int] = {
        "kty": "EC",
        "alg": "ECDSA",
        "crv": ecdsa.SECP256k1.__str__(),
        "use": "sig",
        "kid": kid,
        "x": encodedX,
        "y": encodedY,
    }

    with open(jwks_json_filepath, "r+") as jwks_json_file:
        jwks_contents: list[dict[str, str | int]] = ujson.loads(jwks_json_file.read())[
            "keys"
        ]
        jwks_contents.append(keyMapping)
        length: int = len(jwks_contents)

        if enforce_capacity and length > capacity:
            jwks_contents: list[dict[str, str | int]] = jwks_contents[-capacity:]

        jwks_json_file.seek(0)
        jwks_json_file.write(ujson.dumps({"keys": jwks_contents}, indent=2))
        jwks_json_file.truncate()


def write_ecdsa_pair(
    private_dir: Path,
    public_dir: Path,
    private_key: ecdsa.SigningKey | bytes | bytearray,
    public_key: ecdsa.VerifyingKey | bytes | bytearray,
    key_id: int | str,
    fname_template: str = "{key_type}_{key_id}_key.pem",
) -> None:
    """### Write the private and public keys in their respective PEM files

    #### parameters:\n
    private_dir: Directory to store private key's .pem file in\n
    public_dir: Directory to store public key's .pem file in\n
    private_key: Signing key\n
    public_key: Verificiation key\n
    key_id: Unique numeric ID for this key pair\n
    fname_template: File naming template
    """
    private_buffer: bytes | bytearray = (
        private_key.to_pem()
        if isinstance(private_key, ecdsa.SigningKey)
        else private_key
    )
    public_buffer: bytes | bytearray = (
        public_key.to_pem()
        if isinstance(public_key, ecdsa.VerifyingKey)
        else public_key
    )
    private_dir.joinpath(
        fname_template.format(key_type="private", key_id=key_id)
    ).write_bytes(private_buffer)

    public_dir.joinpath(
        fname_template.format(key_type="public", key_id=key_id)
    ).write_bytes(public_buffer)


def initialize_active_key(
    private_directory: Path,
    public_directory: Path,
    keydata_repository: KeydataRepository,
) -> KeyData:
    active_kid, sk, vk = generate_ecdsa_pair()

    # Persist to PEM, and DB (JWKS done at end)
    write_ecdsa_pair(
        private_dir=private_directory,
        public_dir=public_directory,
        private_key=sk,
        public_key=vk,
        key_id=int(active_kid),
    )

    active_key: KeyData = keydata_repository.insert_keydata(
        active_kid, sk, vk, "ES256", ecdsa.SECP256k1, returning=True
    )
    return active_key
