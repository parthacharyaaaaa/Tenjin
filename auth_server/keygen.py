import ecdsa
from hashlib import sha512
import os
from cryptography.fernet import Fernet

def generate_ecdsa_pair() -> tuple[bytes, bytes]:
    '''Generate signing and verification ECDSA key pair'''
    signingKey: ecdsa.SigningKey = ecdsa.SigningKey.generate(curve=ecdsa.SECP256k1, hashfunc=sha512)
    verificiationKey: ecdsa.VerifyingKey = signingKey.get_verifying_key()

    return signingKey.to_pem(), verificiationKey.to_pem()

def write_ecdsa_pair(privateDir: os.PathLike, staticDir: os.PathLike,
                     encryption_key: bytes,
                     private_key: bytes, public_key: bytes, key_id: int,
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

    encryptedPrivateKey: bytes = fernet.encrypt(private_key)
    privateFpath: os.PathLike = os.path.join(privateDir, fname_template.format(key_type='private', key_id=key_id))
    publicFpath: os.PathLike = os.path.join(privateDir, fname_template.format(key_type='public', key_id=key_id))

    with open(privateFpath, 'wb+') as privatePemFile:
        privatePemFile.write(encryptedPrivateKey)

    with open(publicFpath, 'wb+') as publicPemFile:
        publicPemFile.write(public_key)

    os.chmod(privateFpath, 0o600)