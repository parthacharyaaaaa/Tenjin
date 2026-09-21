import secrets
from typing import Final

from cryptography.hazmat.primitives.asymmetric import ec

from auth_server.config.sub_config import KeyConfigModel


def generate_ecdsa_pair(
    key_config: KeyConfigModel,
) -> tuple[str, ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey]:
    """Generate signing and verification ECDSA key pair"""
    private_key: Final[ec.EllipticCurvePrivateKey] = ec.generate_private_key(
        key_config.EC_TYPE
    )
    public_key: Final[ec.EllipticCurvePublicKey] = private_key.public_key()
    key_id: Final[str] = secrets.token_hex(key_config.KEY_IDENTIFIER_LENGTH)
    return key_id, private_key, public_key
