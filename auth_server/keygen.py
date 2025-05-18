import ecdsa
from hashlib import sha512
import os
from cryptography.fernet import Fernet
import base64
import ujson

def int_to_base64url(n: int) -> str:
    byte_length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(byte_length, 'big')).rstrip(b'=').decode('ascii')

def generate_ecdsa_pair() -> tuple[ecdsa.SigningKey, ecdsa.VerifyingKey]:
    '''Generate signing and verification ECDSA key pair'''
    signingKey: ecdsa.SigningKey = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1, hashfunc=sha512)
    verificiationKey: ecdsa.VerifyingKey = signingKey.get_verifying_key()

    return signingKey, verificiationKey

def update_jwks(vk: ecdsa.VerifyingKey, kid: int,
                jwks_json_filepath: os.PathLike,
                enforce_capacity: bool = True,
                capacity: int = 3) -> None:
    '''Updates the JWKS JSON file to include the given public key as the latest key'''
    keyMapping: dict[str, str|int] = {'kty' : 'EC', 'alg' : 'ECDSA', 'crv' : ecdsa.SECP256k1.__str__(), 'use' :'sig', 'kid' : kid}
    encodedX, encodedY = int_to_base64url(vk.pubkey.point.x()), int_to_base64url(vk.pubkey.point.y())

    keyMapping.update({'x' : encodedX, 'y' : encodedY})

    with open(jwks_json_filepath, 'r+') as jwks_json_file:
        jwks_contents: list[dict[str, str|int]] = ujson.loads(jwks_json_file.read())['keys']
        jwks_contents.append(keyMapping)
        length: int = len(jwks_contents)

        if enforce_capacity and length > capacity:
            jwks_contents: list[dict[str, str|int]] = jwks_contents[-capacity:]
        
        jwks_json_file.seek(0)
        jwks_json_file.write(ujson.dumps(jwks_contents))
        ujson.dump({'keys' : jwks_contents}, jwks_json_filepath)
        jwks_json_file.truncate()

def write_ecdsa_pair(privateDir: os.PathLike, staticDir: os.PathLike,
                     encryption_key: bytes,
                     private_key: ecdsa.SigningKey, public_key: ecdsa.VerifyingKey, key_id: int,
                     fname_template: str = '{key_type}_{key_id}_key.pem') -> None:
    ''' ### Write the private and public keys in their respective PEM files
    
    #### parameters:\n
    privateDir: Directory to store private key's .pem file in\n
    staticDir: Directory to store public key's .pem file in\n
    encryption_key: Symmetric key to encrypt the private key's .pem file\n
    private_key: Signing key\n
    public_key: Verificiation key\n
    key_id: Unique numeric ID for this key pair\n
    fname_template: File naming template
    '''
    fernet = Fernet(encryption_key)

    encryptedPrivateKey: bytes = fernet.encrypt(private_key.to_pem())
    privateFpath: os.PathLike = os.path.join(privateDir, fname_template.format(key_type='private', key_id=key_id))
    publicFpath: os.PathLike = os.path.join(staticDir, fname_template.format(key_type='public', key_id=key_id))

    with open(privateFpath, 'wb+') as privatePemFile:
        privatePemFile.write(encryptedPrivateKey)

    with open(publicFpath, 'wb+') as publicPemFile:
        publicPemFile.write(public_key.to_pem())

    os.chmod(privateFpath, 0o600)