from flask import Flask
from flask.cli import with_appcontext
from auth_server.config import flaskconfig
import time
import os, ujson
from sqlalchemy import text
import ecdsa
from datetime import datetime
from auxillary.utils import generic_error_handler, to_base64url
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair
from auth_server.key_container import KeyMetadata
from cryptography.fernet import Fernet
import traceback

APP_CTX_CWD: os.PathLike = os.path.dirname(__file__)
def create_app() -> Flask:
    auth_app = Flask('auth_app', instance_path=os.path.join(APP_CTX_CWD, 'instance'), static_folder=os.path.join(APP_CTX_CWD, 'static'))
    auth_app.config.from_object(flaskconfig)
    auth_app.pid = os.getpid()

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # SQLAlchemy
    from auth_server.models import db
    from flask_migrate import Migrate
    db.init_app(auth_app)
    migrate = Migrate(auth_app, db)

    # Extensions
    from auth_server.redis_manager import init_redis, init_syncedstore
    init_redis(auth_app)
    init_syncedstore(auth_app)
    from auth_server.redis_manager import RedisInterface, SyncedStore

    from auth_server.token_manager import init_token_manager

    isMaster: bool = bool(SyncedStore.set('AUTH_BOOTUP_MASTER', auth_app.pid, nx=True))

    if not isMaster:
        # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
        while SyncedStore.get('AUTH_BOOTUP_MASTER'):
            time.sleep(1)

        if SyncedStore.get('ABORT'):
            print(f'[AUTH {auth_app.pid}] Master failed to setup key configuration, aborting...')
            raise AssertionError('Master failed to set up key configuration')
        
        # Once lock is released, slave worker only needs to consult database and write to its own memory
        kvs_mapping: dict[str, KeyMetadata] = {}
        with auth_app.app_context():
            with db.engine.connect() as conn:
                res: tuple[tuple] = conn.execute(text('''SELECT kid, public_pem, private_pem, alg, epoch, rotated_out_at
                                                      FROM keydata
                                                      WHERE expired_at IS NULL
                                                      ORDER BY epoch DESC LIMIT 3;''')).fetchall()
                for keyData in res:
                    kvs_mapping[keyData[0]] = KeyMetadata(*keyData[1:])
        
        # Initialize token manager 
        init_token_manager(kvs_mapping, RedisInterface)

    
    # Current worker process is the master, and is responsible for handling JWKS and key synchronization on bootup
    else:
        try:
            print(f'[AUTH {auth_app.pid}] Serving as master')
            initialMap: dict = None

            # Set filepaths
            JWKS_FPATH: os.PathLike = os.path.join(APP_CTX_CWD, 'instance', auth_app.config['JWKS_FILENAME'])
            private_fpath: os.PathLike = os.path.join(APP_CTX_CWD, 'instance', 'keys')
            public_fpath: os.PathLike = os.path.join(APP_CTX_CWD, auth_app.static_folder, 'keys')

            # Consult DB
            kvs_mapping: dict[str, KeyMetadata] = {}
            writeBuffer: list[dict[str, str|int]] = []
            with auth_app.app_context():
                with db.engine.connect() as conn:
                    res = conn.execute(text('''SELECT kid, public_pem, private_pem, curve, alg, epoch, rotated_out_at
                                 FROM keydata
                                 WHERE expired_at IS NULL
                                 ORDER BY EPOCH DESC;''')).fetchall()

                    if not res:
                        # No valid keys in DB, master must create new pair
                        print(f'[AUTH {auth_app.pid}] Creating new key pair')
                        kid, sk, vk = generate_ecdsa_pair()

                        # Persist to PEM, and DB (JWKS done at end)
                        write_ecdsa_pair(privateDir=private_fpath, staticDir=public_fpath,
                                        encryption_key=auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'],
                                        private_key=sk, public_key=vk, key_id=kid)
                            
                        conn.execute(text('''INSERT INTO keydata (kid, alg, curve, epoch, private_pem, public_pem)
                                        VALUES (:kid, :alg, :curve, :epoch, :private_pem, :public_pem)'''),
                                        {'kid':kid, 'alg':'ES256', 'curve':str(ecdsa.SECP256k1), 'epoch':datetime.now(), 'private_pem':sk.to_pem(), 'public_pem':vk.to_pem()})
                        conn.commit()
                        kvs_mapping[kid] = KeyMetadata(PUBLIC_PEM=vk.to_pem(), PRIVATE_PEM=sk.to_pem(), ALGORITHM='ES256', EPOCH=time.time())
                        point = vk.pubkey.point
                        encodedX, encodedY = to_base64url(int(point.x())), to_base64url(int(point.y()))
                        writeBuffer.append({"kty": "EC", "alg": 'ES256',"crv": str(ecdsa.SECP256k1), "use": "sig", "kid": kid,
                                            "x" : encodedX, 
                                            "y": encodedY})

                    else:
                        # Atleast 1 non-expired key exists in DB
                        print(f'[AUTH {auth_app.pid}] Active key(s) found, loading into memory...')
                        if len(res) > 3:
                            # Invalidate older keys
                            conn.execute(text(f'UPDATE keydata SET expired_at = {datetime.now()} WHERE epoch < {res[2][-2]}'))
                            conn.commit()
                            res = res[:3]
                
                        fernet: Fernet = Fernet(auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'])
                        for keyData in res:
                            # Cast to bytes because memoryview is incompatible with ecdsa.VerifiyingKey.from_pem()
                            public_pem:bytes = bytes(keyData[1])
                            private_pem:bytes = bytes(keyData[2])
                            kvs_mapping[keyData[0]] = KeyMetadata(public_pem, private_pem, keyData[4], keyData[5], keyData[6])

                            # Check if PEM files exist, if not then write new ones
                            privatePemFile: os.PathLike = os.path.join(private_fpath, f'private_{keyData[0]}_key.pem')
                            publicPemFile: os.PathLike = os.path.join(public_fpath, f'public_{keyData[0]}_key.pem')
                            if not os.path.isfile(privatePemFile):
                                print(f'[AUTH {auth_app.pid}] PEM file {privatePemFile} active in DB but missing, recreating...')
                                with open(privatePemFile, 'wb+') as newPEM:
                                    newPEM.write(fernet.encrypt(private_pem))

                            if not os.path.isfile(publicPemFile):
                                print(f'[AUTH {auth_app.pid}] PEM file {publicPemFile} active in DB but missing, recreating...')
                                with open(publicPemFile, 'wb+') as newPEM:
                                    newPEM.write(public_pem)

                            # Append data to writeBuffer
                            point = ecdsa.VerifyingKey.from_pem(public_pem).pubkey.point
                            writeBuffer.append({"kty": "EC", "alg": keyData[4],"crv": keyData[3], "use": "sig", "kid": keyData[0],
                                                "x": to_base64url(int(point.x())), 
                                                "y": to_base64url(int(point.y()))})

                    # Rewrite JWKS with writeBuffer
                    print(f'[AUTH {auth_app.pid}] Rewriting JWKS with active keys...')
                    with open(JWKS_FPATH, 'w') as jwks_file:
                        jwks_file.write(ujson.dumps({'keys' : writeBuffer}, indent=2))

                    # Initialize token manager
                    init_token_manager(kvs_mapping, RedisInterface)
                    print(f'[AUTH {auth_app.pid}] Master process bootup complete!')
        except Exception as e:
            print(f'[AUTH {auth_app.pid}] Master worker has encountered an irrecovarable error, details: ')
            print(traceback.format_exc())
            SyncedStore.set('ABORT', 1, ex=120)
            raise RuntimeError('Master bootup failed') from e
        finally:
            # Finally, remove the flag from Redis to allow slave workers to continue bootup
            SyncedStore.delete('AUTH_BOOTUP_MASTER')
        
    # Blueprints
    from auth_server.blueprint_routes import auth
    from auth_server.blueprint_cmd import cmd
    auth_app.register_blueprint(cmd)
    auth_app.register_blueprint(auth)

    return auth_app