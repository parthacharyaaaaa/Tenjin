from flask import Flask
from auth_server.config import flaskconfig
import time
import os, ujson
import ecdsa
from auxillary.utils import generic_error_handler
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair
from auth_server.key_container import KeyMetadata
from cryptography.fernet import Fernet

APP_CTX_CWD: os.PathLike = os.path.dirname(__file__)
def create_app() -> Flask:
    auth_app = Flask('auth_app', instance_path=os.path.join(APP_CTX_CWD, 'instance'), static_folder=os.path.join(APP_CTX_CWD, 'static'))
    auth_app.config.from_object(flaskconfig)

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # Extensions
    from auth_server.redis_manager import init_redis
    init_redis(auth_app)

    from auth_server.redis_manager import RedisInterface
    from auth_server.token_manager import init_token_manager

    # Load JWKS on startup, if none found, assume fresh start and initiate a new store
    #TODO: Set master system because Gunicorn will start up multiple workers, and we only need a single key pair on fresh starts (Redis flag with 'master:os.pid()' should do it)
    initialMap: dict = None

    # Set filepaths
    JWKS_FPATH: os.PathLike = os.path.join(APP_CTX_CWD, 'instance', auth_app.config['JWKS_FILENAME'])
    private_fpath: os.PathLike = os.path.join(APP_CTX_CWD, 'instance', 'keys')
    public_fpath: os.PathLike = os.path.join(APP_CTX_CWD, auth_app.static_folder, 'keys')

    # Lets get started >:3
    with open(JWKS_FPATH, 'r') as jwks_file:
        content: str = jwks_file.read()
        if content:
            initialMap: dict = ujson.loads(content)

    print(initialMap)
    if not initialMap:
        kid, sk, vk = generate_ecdsa_pair()

        # Persist to PEM and JWKS
        write_ecdsa_pair(privateDir=private_fpath, staticDir=public_fpath,
                         encryption_key=auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'],
                         private_key=sk, public_key=vk, key_id=kid)

        with open(JWKS_FPATH, 'w') as jwks_file:
            jwks_file.write(ujson.dumps(obj={'keys' :[{'kty' : 'EC',
                                                'alg' : 'ECDSA',
                                                'crv' : str(ecdsa.SECP256k1),
                                                'use' :'sig',
                                                'kid' : kid,
                                                'x' : int(vk.pubkey.point.x()), 'y' : int(vk.pubkey.point.y())}]}, indent=2))
        
        # Finally initialize token manager with the newly generated key metadata
        init_token_manager(kvsMapping={kid:KeyMetadata(PUBLIC_PEM=vk.to_pem(), PRIVATE_PEM=sk.to_pem(), ALGORITHM='ES256', EPOCH=time.time())},
                           redisinterface=RedisInterface)

    elif 'keys' not in initialMap:  # This is such a lazy condition, I'll fix later :P
        # Exit early on invalid JWKS file
        #TODO: Add more rigourous checking for all mappings before loading them into memory
        print("\n[AUTH] Malformmatted JWKS file found, exiting...")
        raise ValueError('Malformmatted JWKS file')
        
    else:
        # Valid JWKS file, safe to load into memory and into token manager
        kvs_mapping: dict = {}
        fernet: Fernet = Fernet(auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'])  # Private PEM files are encrypted symmetrically
        
        # Load key data mapping for token manager
        for idx, keyData in enumerate(initialMap['keys']):
            #TODO: Add slicing for latest n keys only. (Poor intern implemented the bloody thing with win32, bless his heart I couldn't tell him that we deploy on Alpine lulz)
            privatePem: bytes = None
            publicPem: bytes = None

            try:
                # Private Pem
                with open(os.path.join(private_fpath, f'private_{keyData["kid"]}_key.pem'), 'rb') as privatePemFile:
                    contents: bytes = privatePemFile.read()
                    privatePem = fernet.decrypt(contents)

                with open(os.path.join(public_fpath, f'public_{keyData["kid"]}_key.pem'), 'rb') as publicPemFile:
                    publicPem = publicPemFile.read()
            except FileNotFoundError:
                print(f'[AUTH] Missing PEM files for keys present in JWKS, exiting...')
                raise FileNotFoundError()

            kvs_mapping[keyData['kid']] = KeyMetadata(publicPem, privatePem, 'ES256', keyData.get('epoch', time.time()))

        init_token_manager(kvs_mapping, RedisInterface)
    
    # This flow also stops the function from creating a million PEM files when debugging, super kewl >:3

    # Blueprints
    from auth_server.blueprint_routes import auth
    from auth_server.blueprint_cmd import cmd
    auth_app.register_blueprint(cmd)
    auth_app.register_blueprint(auth)


    return auth_app