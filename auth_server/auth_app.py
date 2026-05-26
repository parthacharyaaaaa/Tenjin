import os
import time
import traceback
import ujson
from datetime import datetime
from pathlib import Path
from typing import Final

import ecdsa

from flask import Flask

from redis import Redis

from sqlalchemy import select, insert, update

from auxillary.utils import generic_error_handler, to_base64url

from auth_server.blueprints import BLUEPRINT_URL_MAPPING
from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_app_config,
    get_token_store_client,
    get_synced_store_client,
    get_database_session,
)
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair
from auth_server.key_container import KeyMetadata
from auth_server.utils import bootup

APP_CTX_CWD: Final[str] = os.path.dirname(__file__)


def create_app() -> Flask:
    auth_app = Flask(
        "auth_app",
        instance_path=os.path.join(APP_CTX_CWD, "instance"),
        static_folder=os.path.join(APP_CTX_CWD, "static"),
    )

    PID: Final[int] = os.getpid()

    config: Final[AppConfig] = get_app_config()

    # Additional filepaths depending on instance/static directories
    config.JWKS.resolve_jwks_directory(config.CORE.instance_path)
    config.JWKS.resolve_public_pem_directory(config.CORE.static_path)
    config.JWKS.resolve_private_pem_directory(config.CORE.instance_path)

    # Error handler
    auth_app.register_error_handler(Exception, generic_error_handler)

    # SQLAlchemy
    from auth_server.models.database import KeyData

    token_store_client: Final[Redis] = get_token_store_client()
    synced_store_client: Final[Redis] = get_synced_store_client()

    from auth_server.token_manager import init_token_manager

    valid_keys_mapping: dict[str, KeyMetadata] = {}
    active_kid: str | None = None
    active_keydata: KeyMetadata | None = None

    isMaster: bool = bool(synced_store_client.set("AUTH_BOOTUP_MASTER", PID, nx=True))

    if not isMaster:
        # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
        while synced_store_client.get("AUTH_BOOTUP_MASTER"):
            time.sleep(1)

        if synced_store_client.get("ABORT"):
            print(f"[AUTH {PID}] Master failed to setup key configuration, aborting...")
            raise AssertionError("Master failed to set up key configuration")

        # Once lock is released, slave worker only needs to consult database and write to its own memory
        with (
            auth_app.app_context()
        ):  # Outside of HTTP context, explicitly provide app context
            keys: list[KeyData] = list(
                db.session.execute(
                    select(KeyData)
                    .where(KeyData.expired_at.is_(None))
                    .order_by(KeyData.epoch.desc())
                    .limit(auth_app.config["JWKS_CAP"])
                )
                .scalars()
                .all()
            )
        for key in keys:
            if key.rotated_out_at:
                # Verification Key
                valid_keys_mapping[key.kid] = KeyMetadata(
                    key.public_pem,
                    key.private_pem,
                    key.alg,
                    key.epoch.timestamp(),
                    key.rotated_out_at.timestamp(),
                )
            else:
                active_kid = key.kid
                active_keydata = KeyMetadata(
                    key.public_pem, key.private_pem, key.alg, key.epoch.timestamp()
                )

        # Initialize token manager
        assert active_kid
        assert active_keydata

        init_token_manager(
            valid_keys_mapping,
            active_kid,
            active_keydata,
            token_store_client,
            synced_store_client,
            db,
            auth_app,
        )

    # Current worker process is the master, and is responsible for handling JWKS and key synchronization on bootup
    else:
        try:
            print(f"[AUTH {PID}] Serving as master")

            # Consult DB
            writeBuffer: list[dict[str, str | int]] = []
            with auth_app.app_context():
                res: list[KeyData] = list(
                    session.execute(
                        select(KeyData)
                        .where(KeyData.expired_at.is_(None))
                        .order_by(KeyData.epoch.desc())
                    )
                    .scalars()
                    .all()
                )

                if not res:
                    # No valid keys in DB, master must create new pair
                    print(f"[AUTH {PID}] Creating new key pair")
                    kid, sk, vk = generate_ecdsa_pair()

                    # Persist to PEM, and DB (JWKS done at end)
                    write_ecdsa_pair(
                        private_dir=config.JWKS.PRIVATE_PEM_DIRECTORY,
                        public_dir=config.JWKS.PUBLIC_PEM_DIRECTORY,
                        private_key=sk,
                        public_key=vk,
                        key_id=int(kid),
                    )

                    db.session.execute(
                        insert(KeyData).values(
                            kid=kid,
                            alg="ES256",
                            curve=str(ecdsa.SECP256k1),
                            epoch=datetime.now(),
                            private_pem=sk.to_pem(),
                            public_pem=vk.to_pem(),
                        )
                    )
                    db.session.commit()
                    active_kid = kid
                    active_keydata = KeyMetadata(
                        PUBLIC_PEM=vk.to_pem(),
                        PRIVATE_PEM=sk.to_pem(),
                        ALGORITHM="ES256",
                        EPOCH=time.time(),
                    )

                    point = vk.pubkey.point
                    encodedX, encodedY = to_base64url(int(point.x())), to_base64url(
                        int(point.y())
                    )
                    writeBuffer.append(
                        {
                            "kty": "EC",
                            "alg": "ES256",
                            "crv": str(ecdsa.SECP256k1),
                            "use": "sig",
                            "kid": kid,
                            "x": encodedX,
                            "y": encodedY,
                        }
                    )

                else:
                    # Atleast 1 non-expired key exists in DB
                    print(f"[AUTH {PID}] Active key(s) found, loading into memory...")
                    if len(res) > auth_app.config["JWKS_CAP"]:
                        # Invalidate older keys
                        db.session.execute(
                            update(KeyData).where(
                                KeyData.epoch
                                < res[auth_app.config["JWKS_CAP"] - 1].epoch
                            )
                        )

                        db.session.commit()
                        res = res[: auth_app.config["JWKS_CAP"]]

                    for keyData in res:
                        public_pem: bytes = keyData.public_pem
                        private_pem: bytes = keyData.private_pem
                        private_pem_file: Path = (
                            config.JWKS.PRIVATE_PEM_DIRECTORY
                            / f"private_{keyData.kid}_key.pem"
                        )
                        public_pem_file: Path = (
                            config.JWKS.PUBLIC_PEM_DIRECTORY
                            / f"public_{keyData.kid}_key.pem"
                        )

                        if keyData.rotated_out_at:
                            # Verification Key
                            valid_keys_mapping[keyData.kid] = KeyMetadata(
                                public_pem,
                                private_pem,
                                keyData.alg,
                                keyData.epoch.timestamp(),
                                keyData.rotated_out_at.timestamp(),
                            )
                            # Ensure that only public pem file exists for this key
                            if private_pem_file.exists():
                                # Private PEM file found, purge
                                print(
                                    f"[AUTH {PID}] Private PEM file {private_pem_file} present for verification key, deleting..."
                                )
                                private_pem_file.unlink()
                            print(
                                f"[AUTH {PID}] Private PEM file {private_pem_file} deleted!"
                            )
                        else:
                            # Signing key
                            active_kid = keyData.kid
                            active_keydata = KeyMetadata(
                                public_pem,
                                private_pem,
                                keyData.alg,
                                keyData.epoch,
                                keyData.rotated_out_at,
                            )

                            # Ensure private PEM file exists
                            if not private_pem_file.exists:
                                print(
                                    f"[AUTH {PID}] Private PEM file {active_kid} for signing key missing in file system, recreating..."
                                )
                                private_pem_file.write_bytes(private_pem)
                                print(
                                    f"[AUTH {PID}] Private PEM file {active_kid} for signing key recreated!"
                                )

                        # Public PEM files should exist for all keys
                        if not public_pem_file.exists():
                            print(
                                f"[AUTH {PID}] Public PEM file {public_pem_file} active in DB but missing in file system, recreating..."
                            )
                            public_pem_file.write_bytes(public_pem)
                            print(
                                f"[AUTH {PID}] Public PEM file {public_pem_file} recreated!"
                            )

                        # Append data to writeBuffer
                        point = ecdsa.VerifyingKey.from_pem(public_pem).pubkey.point
                        writeBuffer.append(
                            {
                                "kty": "EC",
                                "alg": keyData.alg,
                                "crv": keyData.curve,
                                "use": "sig",
                                "kid": keyData.kid,
                                "x": to_base64url(int(point.x())),
                                "y": to_base64url(int(point.y())),
                            }
                        )

                # Lastly, purge any PEM files for expired keys that are somehow still in file system
                expiredKeys: list[str] = list(
                    db.session.execute(
                        select(KeyData.kid).where(KeyData.expired_at.isnot(None))
                    )
                    .scalars()
                    .all()
                )
                for expiredKey in expiredKeys:
                    (
                        config.JWKS.PUBLIC_PEM_DIRECTORY.joinpath(
                            f"public_{expiredKey}_key.pem"
                        ).unlink(missing_ok=True)
                    )

                    (
                        config.JWKS.PRIVATE_PEM_DIRECTORY.joinpath(
                            f"private_{expiredKey}_key.pem"
                        ).unlink(missing_ok=True)
                    )

            # Rewrite JWKS with writeBuffer
            print(f"[AUTH {PID}] Rewriting JWKS...")
            with open(config.JWKS.JWKS_FILEPATH, "w") as jwks_file:
                jwks_file.write(ujson.dumps({"keys": writeBuffer}, indent=2))

            # Initialize token manager
            assert active_kid
            assert active_keydata
            init_token_manager(
                valid_keys_mapping,
                active_kid,
                active_keydata,
                token_store_client,
                synced_store_client,
                db,
                auth_app,
            )
            print(f"[AUTH {PID}] Master process bootup complete!")
        except Exception as e:
            print(
                f"[AUTH {PID}] Master worker has encountered an irrecovarable error, details: "
            )
            print(traceback.format_exc())
            synced_store_client.set("ABORT", 1, ex=120)
            raise RuntimeError("Master bootup failed") from e
        finally:
            # Finally, initialize valid_keys list and remove the flag from Redis to allow slave workers to continue bootup
            assert active_kid
            with synced_store_client.pipeline() as pipe:
                pipe.delete("VALID_KEYS")
                valid_keys: list[str] = list(valid_keys_mapping.keys()) + [active_kid]
                pipe.lpush("VALID_KEYS", *valid_keys)
                pipe.delete("AUTH_BOOTUP_MASTER")
                pipe.execute()

    # Blueprints
    bootup.register_blueprints(
        auth_app, BLUEPRINT_URL_MAPPING, common_prefix=config.CORE.APPLICATION_ROOT
    )

    return auth_app
