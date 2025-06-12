from flask import Flask
from auth_server.config import flaskconfig
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair
from auth_server.key_container import KeyMetadata
from auxillary.utils import generic_error_handler, to_base64url
from cryptography.fernet import Fernet
from sqlalchemy import select, insert, update
from datetime import datetime
import time
import os
import ujson
import ecdsa
import traceback
import toml
from typing import Any

APP_CTX_CWD: os.PathLike = os.path.dirname(__file__)
def create_app() -> Flask:
    auth_app = Flask('auth_app', instance_path=os.path.join(APP_CTX_CWD, 'instance'), static_folder=os.path.join(APP_CTX_CWD, 'static'))
    auth_app.config.from_object(flaskconfig)
    auth_app.pid = os.getpid()

    # Additional filepaths depending on instance/static directories
    auth_app.config['JWKS_FPATH'] = os.path.join(auth_app.instance_path, 'jwks.json')
    auth_app.config['PUBLIC_PEM_DIRECTORY'] = os.path.join(auth_app.static_folder, 'keys')
    auth_app.config['PRIVATE_PEM_DIRECTORY'] = os.path.join(auth_app.instance_path, 'keys') #NOTE: Is this really safe?

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # SQLAlchemy
    from auth_server.models import db, KeyData
    from flask_migrate import Migrate
    db.init_app(auth_app)
    migrate = Migrate(auth_app, db)

    # Extensions
    redis_config_filepath: os.PathLike = os.path.join(APP_CTX_CWD, 'config', os.environ['REDIS_CONFIG_FILENAME'])
    redis_config_kwargs: dict[str, dict[str, Any]] = toml.load(f=redis_config_filepath)

    from auth_server.redis_manager import init_redis, init_syncedstore
    init_redis(**redis_config_kwargs['token_store'])
    init_syncedstore(**redis_config_kwargs['synced_store'])
    from auth_server.redis_manager import RedisInterface, SyncedStore

    from auth_server.token_manager import init_token_manager
    valid_keys_mapping: dict[str, KeyMetadata] = {}
    active_kid: str = None
    active_keydata: KeyMetadata = None

    isMaster: bool = bool(SyncedStore.set('AUTH_BOOTUP_MASTER', auth_app.pid, nx=True))

    if not isMaster:
        # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
        while SyncedStore.get('AUTH_BOOTUP_MASTER'):
            time.sleep(1)

        if SyncedStore.get('ABORT'):
            print(f'[AUTH {auth_app.pid}] Master failed to setup key configuration, aborting...')
            raise AssertionError('Master failed to set up key configuration')
        
        # Once lock is released, slave worker only needs to consult database and write to its own memory
        with auth_app.app_context():    # Outside of HTTP context, explicitly provide app context
            keys: list[KeyData] = db.session.execute(select(KeyData)
                                                     .where(KeyData.expired_at.is_(None))
                                                     .order_by(KeyData.epoch.desc())
                                                     .limit(auth_app.config['JWKS_CAP'])
                                                     ).scalars().all()
        for key in keys:
            if key.rotated_out_at:
                # Verification Key
                valid_keys_mapping[key.kid] = KeyMetadata(key.public_pem, key.private_pem, key.alg, key.epoch, key.rotated_out_at)
            else:
                active_kid = key.kid
                active_keydata =  KeyMetadata(key.public_pem, key.private_pem, key.alg, key.epoch)\
        
        # Initialize token manager 
        init_token_manager(valid_keys_mapping, active_kid, active_keydata, RedisInterface, SyncedStore, db)

    # Current worker process is the master, and is responsible for handling JWKS and key synchronization on bootup
    else:
        try:
            print(f'[AUTH {auth_app.pid}] Serving as master')
            initialMap: dict = None

            # Set filepaths
            JWKS_FPATH: os.PathLike = os.path.join(auth_app.instance_path, auth_app.config['JWKS_FILENAME'])
            private_fpath: os.PathLike = os.path.join(auth_app.instance_path, 'keys')
            public_fpath: os.PathLike = os.path.join(auth_app.static_folder, 'keys')

            # Consult DB
            writeBuffer: list[dict[str, str|int]] = []
            with auth_app.app_context():
                res: list[KeyData] = db.session.execute(select(KeyData)
                                                            .where(KeyData.expired_at.is_(None))
                                                            .order_by(KeyData.epoch.desc())
                                                            ).scalars().all()

                if not res:
                    # No valid keys in DB, master must create new pair
                    print(f'[AUTH {auth_app.pid}] Creating new key pair')
                    kid, sk, vk = generate_ecdsa_pair()

                    # Persist to PEM, and DB (JWKS done at end)
                    write_ecdsa_pair(privateDir=private_fpath, staticDir=public_fpath,
                                    encryption_key=auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'],
                                    private_key=sk, public_key=vk, key_id=kid)
                        
                    db.session.execute(insert(KeyData)
                                        .values(kid=kid, alg='ES256', curve=str(ecdsa.SECP256k1), epoch=datetime.now(),
                                                private_pem=sk.to_pem(), public_pem=vk.to_pem()))
                    db.session.commit()
                    active_kid = kid
                    active_keydata = KeyMetadata(PUBLIC_PEM=vk.to_pem(), PRIVATE_PEM=sk.to_pem(), ALGORITHM='ES256', EPOCH=time.time())

                    point = vk.pubkey.point
                    encodedX, encodedY = to_base64url(int(point.x())), to_base64url(int(point.y()))
                    writeBuffer.append({"kty": "EC", "alg": 'ES256',"crv": str(ecdsa.SECP256k1), "use": "sig", "kid": kid,
                                        "x" : encodedX, 
                                        "y": encodedY})

                else:
                    # Atleast 1 non-expired key exists in DB
                    print(f'[AUTH {auth_app.pid}] Active key(s) found, loading into memory...')
                    if len(res) > auth_app.config['JWKS_CAP']:
                        # Invalidate older keys
                        db.session.execute(update(KeyData)
                                            .where(KeyData.epoch < res[auth_app.config['JWKS_CAP']-1].epoch))

                        db.session.commit()
                        res = res[:auth_app.config['JWKS_CAP']]
            
                    fernet: Fernet = Fernet(auth_app.config['PRIVATE_PEM_ENCRYPTION_KEY'])
                    for keyData in res:
                        public_pem:bytes = keyData.public_pem
                        private_pem:bytes = keyData.private_pem
                        privatePemFile: os.PathLike = os.path.join(private_fpath, f'private_{keyData.kid}_key.pem')
                        publicPemFile: os.PathLike = os.path.join(public_fpath, f'public_{keyData.kid}_key.pem')

                        if keyData.rotated_out_at:
                            # Verification Key
                            valid_keys_mapping[keyData.kid] = KeyMetadata(public_pem, private_pem, keyData.alg, keyData.epoch, keyData.rotated_out_at)
                            # Ensure that only public pem file exists for this key
                            if os.path.isfile(privatePemFile):
                                # Private PEM file found, purge
                                print(f'[AUTH {auth_app.pid}] Private PEM file {privatePemFile} present for verification key, deleting...')
                                os.remove(privatePemFile)
                            print(f'[AUTH {auth_app.pid}] Private PEM file {privatePemFile} deleted!')
                        else:
                            # Signing key
                            active_kid = keyData.kid
                            active_keydata = KeyMetadata(public_pem, private_pem, keyData.alg, keyData.epoch, keyData.rotated_out_at)
                            
                            # Ensure private PEM file exists
                            if not os.path.isfile(privatePemFile):
                                print(f'[AUTH {auth_app.pid}] Private PEM file {publicPemFile} for signing key missing in file system, recreating...')
                                with open(privatePemFile, 'wb') as new_private_pem:
                                    new_private_pem.write(fernet.encrypt(private_pem))
                                print(f'[AUTH {auth_app.pid}] Private PEM file {publicPemFile} for signing key recreated!')
                        
                        # Public PEM files should exist for all keys
                        if not os.path.isfile(publicPemFile):
                            print(f'[AUTH {auth_app.pid}] Public PEM file {publicPemFile} active in DB but missing in file system, recreating...')
                            with open(publicPemFile, 'wb+') as newPEM:
                                newPEM.write(public_pem)
                            print(f'[AUTH {auth_app.pid}] Public PEM file {publicPemFile} recreated!')

                        # Append data to writeBuffer
                        point = ecdsa.VerifyingKey.from_pem(public_pem).pubkey.point
                        writeBuffer.append({"kty": "EC", "alg": keyData.alg,"crv": keyData.curve, "use": "sig", "kid": keyData.kid,
                                            "x": to_base64url(int(point.x())),
                                            "y": to_base64url(int(point.y()))})

                # Lastly, purge any PEM files for expired keys that are somehow still in file system
                expiredKeys: list[str] = db.session.execute(select(KeyData.kid)
                                                            .where(KeyData.expired_at.isnot(None))
                                                            ).scalars().all()
                for expiredKey in expiredKeys:
                    public_pem_fpath: os.PathLike = os.path.join(public_fpath, f'public_{expiredKey}_key.pem')
                    private_pem_fpath: os.PathLike = os.path.join(private_fpath, f'private_{expiredKey}_key.pem')
                    if os.path.isfile(public_pem_fpath):
                        os.remove(public_pem_fpath)
                    if os.path.isfile(private_pem_fpath):
                        os.remove(private_pem_fpath)
                
            # Rewrite JWKS with writeBuffer
            print(f'[AUTH {auth_app.pid}] Rewriting JWKS...')
            with open(JWKS_FPATH, 'w') as jwks_file:
                jwks_file.write(ujson.dumps({'keys' : writeBuffer}, indent=2))

            # Initialize token manager
            init_token_manager(valid_keys_mapping, active_kid, active_keydata, RedisInterface, SyncedStore, db)
            print(f'[AUTH {auth_app.pid}] Master process bootup complete!')
        except Exception as e:
            print(f'[AUTH {auth_app.pid}] Master worker has encountered an irrecovarable error, details: ')
            print(traceback.format_exc())
            SyncedStore.set('ABORT', 1, ex=120)
            raise RuntimeError('Master bootup failed') from e
        finally:
            # Finally, initialize valid_keys list and remove the flag from Redis to allow slave workers to continue bootup
            with SyncedStore.pipeline() as pipe:
                pipe.delete('VALID_KEYS')
                valid_keys: list[str] = list(valid_keys_mapping.keys()) + [active_kid]
                pipe.lpush('VALID_KEYS', *valid_keys)
                pipe.delete('AUTH_BOOTUP_MASTER')
                pipe.execute()
        
    # Blueprints
    from auth_server.blueprint_routes import auth
    from auth_server.blueprint_cmd import cmd
    auth_app.register_blueprint(cmd)
    auth_app.register_blueprint(auth)

    return auth_app