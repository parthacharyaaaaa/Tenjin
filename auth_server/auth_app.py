from flask import Flask
from auth_server.config import flaskconfig
import os, ujson
import ecdsa
from auxillary.utils import generic_error_handler
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair

APP_CTX_CWD: os.PathLike = os.path.dirname(__file__)
def create_app() -> Flask:
    auth_app = Flask('auth_app', instance_path=os.path.join(APP_CTX_CWD, 'instance'), static_folder=os.path.join(APP_CTX_CWD, 'static'))
    auth_app.config.from_object(flaskconfig)

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # Extensions
    from auth_server.redis_manager import init_redis
    init_redis(auth_app)
    
    from auth_server.token_manager import init_token_manager
    from auth_server.redis_manager import RedisInterface
    init_token_manager(auth_app, RedisInterface)

    # Blueprints
    from auth_server.blueprint_routes import auth
    from auth_server.blueprint_cmd import cmd
    auth_app.register_blueprint(cmd)
    auth_app.register_blueprint(auth)

    # Load JWKS on startup, if none found, assume fresh start and initiate a new store
    with open(os.path.join(APP_CTX_CWD, 'instance', auth_app.config['JWKS_FILENAME']), 'w+') as jwks_file:
        content: str = jwks_file.read()
        if content:
            initialMap: dict = ujson.loads(content)

        if not content or not initialMap:   # Empty mappings also included
            # Create a new key pair
            kid, sk, vk = generate_ecdsa_pair()

            # Persist to PEM and JWKS
            write_ecdsa_pair(auth_app.instance_path, auth_app.static_folder, auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'], sk, vk, kid)

            jwks_file.seek(0)
            jwks_file.write(ujson.dumps(obj={'kty' : 'EC',
                                             'alg' : 'ECDSA',
                                             'crv' : ecdsa.SECP256k1.__str__(),
                                             'use' :'sig',
                                             'kid' : kid, 'x' : int(vk.pubkey.point.x()), 'y' : int(vk.pubkey.point.y())},
                                        indent=4))
            jwks_file.truncate()

        elif 'keys' not in initialMap:
            print("\n[AUTH] Malformmatted JWKS file found, exiting...")
            raise ValueError('Malformmatted JWKS file')
        
        else:
            auth_app.config['JWKS_KV_MAPPING'] = initialMap

    return auth_app