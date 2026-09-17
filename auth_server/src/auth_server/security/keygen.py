import asyncio
import os
import secrets
from pathlib import Path
from typing import Sequence

import orjson
from auxillary.utils import to_base64url
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_public_key,
)

from auth_server.models.database import KeyData
from auth_server.repositories.keydata import KeydataRepository


def generate_ecdsa_pair() -> tuple[
    str, ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey
]:
    """Generate signing and verification ECDSA key pair"""
    signing_key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256K1())
    verification_key: ec.EllipticCurvePublicKey = signing_key.public_key()
    kid: str = str(secrets.randbelow(10_000_000))

    return kid, signing_key, verification_key


def initialize_jwks(jwks_filepath: Path, keys: Sequence[KeyData]) -> None:
    jwks_contents: list[dict[str, str | int]] = []
    for key in keys:
        verification_key: PublicKeyTypes = load_pem_public_key(key.public_pem)
        if not isinstance(verification_key, ec.EllipticCurvePublicKey):
            raise TypeError("Expected an elliptic-curve public key")
        public_numbers: ec.EllipticCurvePublicNumbers = (
            verification_key.public_numbers()
        )
        jwks_contents.append(
            {
                "kty": "EC",
                "alg": key.alg,
                "crv": key.curve,
                "use": "sig",
                "kid": key.kid,
                "x": to_base64url(public_numbers.x),
                "y": to_base64url(public_numbers.y),
            }
        )

        jwks_filepath.write_bytes(orjson.dumps({"keys": jwks_contents}))


def update_jwks(
    vk: ec.EllipticCurvePublicKey,
    kid: str,
    jwks_json_filepath: os.PathLike,
    enforce_capacity: bool = True,
    capacity: int = 3,
) -> None:
    """Updates the JWKS JSON file to include the given public key as the latest key"""
    public_numbers: ec.EllipticCurvePublicNumbers = vk.public_numbers()
    encoded_x, encoded_y = (
        to_base64url(public_numbers.x),
        to_base64url(public_numbers.y),
    )
    key_mapping: dict[str, str | int] = {
        "kty": "EC",
        "alg": "ECDSA",
        "crv": ec.SECP256K1.name,
        "use": "sig",
        "kid": kid,
        "x": encoded_x,
        "y": encoded_y,
    }

    with open(jwks_json_filepath, "r+") as jwks_json_file:
        jwks_contents: list[dict[str, str | int]] = orjson.loads(jwks_json_file.read())[
            "keys"
        ]
        jwks_contents.append(key_mapping)
        length: int = len(jwks_contents)

        if enforce_capacity and length > capacity:
            jwks_contents: list[dict[str, str | int]] = jwks_contents[-capacity:]

        jwks_json_file.seek(0)
        jwks_json_file.write(orjson.dumps({"keys": jwks_contents}).decode("utf-8"))
        jwks_json_file.truncate()


def write_ecdsa_pair(
    private_dir: Path,
    public_dir: Path,
    private_key: ec.EllipticCurvePrivateKey | bytes | bytearray,
    public_key: ec.EllipticCurvePublicKey | bytes | bytearray,
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
        private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        if isinstance(private_key, ec.EllipticCurvePrivateKey)
        else private_key
    )
    public_buffer: bytes | bytearray = (
        public_key.public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
        if isinstance(public_key, ec.EllipticCurvePublicKey)
        else public_key
    )
    private_pem_path: Path = private_dir.joinpath(
        fname_template.format(key_type="private", key_id=key_id)
    )
    private_pem_path.touch()
    private_pem_path.write_bytes(private_buffer)

    public_pem_path: Path = public_dir.joinpath(
        fname_template.format(key_type="public", key_id=key_id)
    )
    public_pem_path.touch()
    public_pem_path.write_bytes(public_buffer)


async def initialize_active_key(
    private_directory: Path,
    public_directory: Path,
    keydata_repository: KeydataRepository,
) -> KeyData:
    active_kid, sk, vk = generate_ecdsa_pair()

    if not await asyncio.to_thread(private_directory.exists):
        await asyncio.to_thread(private_directory.mkdir, parents=True)
    if not await asyncio.to_thread(public_directory.exists):
        await asyncio.to_thread(public_directory.mkdir, parents=True)

    # Persist to PEM, and DB (JWKS done at end)
    write_ecdsa_pair(
        private_dir=private_directory,
        public_dir=public_directory,
        private_key=sk,
        public_key=vk,
        key_id=int(active_kid),
    )

    active_key: KeyData = await keydata_repository.insert_keydata(
        active_kid, sk, vk, "ES256", ec.SECP256K1(), returning=True
    )
    return active_key
