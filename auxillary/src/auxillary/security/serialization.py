from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def pem_serialize_public_key(
    public_key: ec.EllipticCurvePublicKey, format: PublicFormat
) -> bytes:
    return public_key.public_bytes(Encoding.PEM, format)


def pem_serialize_private_key(
    private_key: ec.EllipticCurvePrivateKey,
    format: PrivateFormat,
    encryption_algorithm: KeySerializationEncryption = NoEncryption(),
) -> bytes:
    return private_key.private_bytes(Encoding.PEM, format, encryption_algorithm)
