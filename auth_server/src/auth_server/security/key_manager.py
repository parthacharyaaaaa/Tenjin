import asyncio
import secrets
from collections.abc import AsyncGenerator, MutableMapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, IntFlag
from pathlib import Path
from typing import Final, Protocol

import aiofiles
import orjson
from auxillary.mixins.metaclass import AntiSingletonMixin
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
from redis.asyncio.client import Redis

from auth_server.config.sub_config import JWKSConfigModel, KeyConfigModel
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.security.key_container import KeyMetadata
from auth_server.security.token_manager import TokenManager
from auth_server.strings import (
    GENERIC_SEPARATOR,
    SelectionLockOption,
    SyncedStoreStrings,
)


class KeyOperationLocks(IntFlag):
    JWKS_WRITE = 0b0001
    INVALIDATION_UPDATE = 0b0010
    KEYSTORE_CLEAN = 0b0100


class LockTIme(IntEnum):
    LOW = 10
    LMEDIUM = 25
    MEDIUM = 50
    HMEDIUM = 75
    HIGH = 100


class SupportsTransactionalBlocks(Protocol):
    def transactional_block(
        self,
    ) -> AbstractAsyncContextManager[None, bool | None]: ...


@dataclass(slots=True, frozen=True)
class FileSystemKeyManager(AntiSingletonMixin):
    jwks_config: JWKSConfigModel
    keys_config: KeyConfigModel
    pem_filename_template: str = field(default="{key_id}_key", kw_only=True)
    _rewrite_buffer: dict[Path, bytes | bytearray | str] = field(default_factory=dict)
    _deletion_buffer: list[Path] = field(default_factory=list)

    @asynccontextmanager
    async def transactional_block(
        self,
    ) -> AsyncGenerator[None]:
        try:
            yield
        except Exception:
            await asyncio.gather(
                *(
                    asyncio.to_thread(
                        path.write_text  # pyrefly: ignore[bad-argument-type]
                        if isinstance(buffer, str)
                        else path.write_bytes,
                        buffer,  # pyrefly: ignore[bad-argument-type]
                    )
                    for path, buffer in self._rewrite_buffer.items()
                )
            )
            await asyncio.gather(
                *(
                    asyncio.to_thread(path.unlink, missing_ok=True)
                    for path in self._deletion_buffer
                )
            )
            raise
        finally:
            self._deletion_buffer.clear()
            self._rewrite_buffer.clear()

    def _insure_file_rewrite(
        self,
        filepath: Path,
        *,
        decoded: bool = True,
        string_encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> None:
        if not decoded:
            self._rewrite_buffer[filepath] = filepath.read_bytes()
            return
        self._rewrite_buffer[filepath] = filepath.read_text(
            string_encoding, errors, newline
        )

    def _insure_path_deletion(self, path: Path) -> None:
        self._deletion_buffer.append(path)

    def generate_ecdsa_pair(
        self,
    ) -> tuple[str, ec.EllipticCurvePrivateKey, ec.EllipticCurvePublicKey]:
        private_key: Final[ec.EllipticCurvePrivateKey] = ec.generate_private_key(
            self.keys_config.EC_TYPE
        )
        public_key: Final[ec.EllipticCurvePublicKey] = private_key.public_key()
        key_id: Final[str] = secrets.token_hex(self.keys_config.KEY_IDENTIFIER_LENGTH)
        return key_id, private_key, public_key

    async def initialize_jwks(self, keys: Sequence[KeyPrivateDataResult]) -> None:
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
                    "key_id": key.kid,
                    "x": to_base64url(public_numbers.x),
                    "y": to_base64url(public_numbers.y),
                }
            )

        self._insure_file_rewrite(self.jwks_config.JWKS_FILEPATH)
        await asyncio.to_thread(
            self.jwks_config.JWKS_FILEPATH.write_bytes,
            orjson.dumps({"keys": jwks_contents}),
        )

    async def update_jwks(
        self,
        vk: ec.EllipticCurvePublicKey,
        key_id: str,
        enforce_capacity: bool = True,
    ) -> None:
        """Updates the JWKS JSON file to include the given public key as the latest key"""
        public_numbers: ec.EllipticCurvePublicNumbers = vk.public_numbers()
        encoded_x, encoded_y = (
            to_base64url(public_numbers.x),
            to_base64url(public_numbers.y),
        )
        key_mapping: dict[str, str] = {
            "kty": "EC",
            "alg": "ECDSA",
            "crv": ec.SECP256K1.name,
            "use": "sig",
            "kid": key_id,
            "x": encoded_x,
            "y": encoded_y,
        }

        self._insure_file_rewrite(self.jwks_config.JWKS_FILEPATH)
        async with aiofiles.open(
            self.jwks_config.JWKS_FILEPATH, "r+"
        ) as jwks_json_file:
            jwks_contents: list[dict[str, str]] = orjson.loads(
                await jwks_json_file.read()
            )["keys"]
            jwks_contents.append(key_mapping)
            length: int = len(jwks_contents)

            if enforce_capacity and length > self.keys_config.MAX_VALID_KEYS:
                truncated_keys: list[dict[str, str]] = jwks_contents[
                    : self.keys_config.MAX_VALID_KEYS
                ]
                jwks_contents: list[dict[str, str]] = jwks_contents[
                    -self.keys_config.MAX_VALID_KEYS :
                ]

                await self.delete_key_files(tuple(key["kid"] for key in truncated_keys))

            await jwks_json_file.truncate(0)
            await jwks_json_file.seek(0)
            await jwks_json_file.write(
                orjson.dumps({"keys": jwks_contents}).decode("utf-8")
            )

    async def overwrite_jwks(
        self,
        jwks_data: list[dict[str, str]],  # TODO: Make this a pydantic model
    ) -> None:
        self._insure_file_rewrite(self.jwks_config.JWKS_FILEPATH)
        async with aiofiles.open(self.jwks_config.JWKS_FILEPATH, "wb") as jwks_file:
            await jwks_file.write(orjson.dumps(jwks_data))

    async def delete_key_files(
        self,
        key_ids: Sequence[str],
        *,
        delete_private: bool = False,
        delete_public: bool = True,
    ):
        if not (delete_public or delete_private):
            raise ValueError("Atleast 1 deletion flag must be true")

        deletion_paths: list[Path] = []
        for key_id in key_ids:
            if delete_public:
                public_path: Path = self.jwks_config.PUBLIC_PEM_DIRECTORY.joinpath(
                    self.pem_filename_template.format(key_id)
                )
                deletion_paths.append(public_path)
                self._insure_file_rewrite(public_path)
            if delete_private:
                private_path: Path = self.jwks_config.PRIVATE_PEM_DIRECTORY.joinpath(
                    self.pem_filename_template.format(key_id)
                )
                deletion_paths.append(private_path)
                self._insure_file_rewrite(private_path)

        await asyncio.gather(
            *(
                asyncio.to_thread(path.unlink, missing_ok=True)
                for path in deletion_paths
            )
        )

    async def invalidate_keys(self, keys: Sequence[str]) -> None:
        async with aiofiles.open(self.jwks_config.JWKS_FILEPATH, "rb") as jwks_file:
            jwks_data: list[dict[str, str]] = orjson.loads(await jwks_file.read())
        for i, key_data in enumerate(jwks_data.copy()):
            if key_data["kid"] in keys:
                jwks_data.pop(i)

        await self.overwrite_jwks(jwks_data)
        await self.delete_key_files(keys)

    async def write_ecdsa_pair(
        self,
        private_key: ec.EllipticCurvePrivateKey | bytes | bytearray,
        public_key: ec.EllipticCurvePublicKey | bytes | bytearray,
        key_id: int | str,
    ) -> None:
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
        private_pem_path: Path = self.jwks_config.PRIVATE_PEM_DIRECTORY.joinpath(
            self.pem_filename_template.format(key_id=key_id) + ".pem"
        )
        public_pem_path: Path = self.jwks_config.PUBLIC_PEM_DIRECTORY.joinpath(
            self.pem_filename_template.format(key_id=key_id) + ".pem"
        )

        self._insure_path_deletion(private_pem_path)
        self._insure_path_deletion(public_pem_path)
        await asyncio.gather(
            asyncio.to_thread(
                private_pem_path.write_bytes,
                private_buffer,
            ),
            asyncio.to_thread(
                public_pem_path.write_bytes,
                public_buffer,
            ),
        )

    async def initialize_active_key(
        self,
    ) -> None:
        active_kid, sk, vk = self.generate_ecdsa_pair()

        if not await asyncio.to_thread(self.jwks_config.PRIVATE_PEM_DIRECTORY.exists):
            self._insure_path_deletion(self.jwks_config.PRIVATE_PEM_DIRECTORY)
            await asyncio.to_thread(
                self.jwks_config.PRIVATE_PEM_DIRECTORY.mkdir, parents=True
            )
        if not await asyncio.to_thread(self.jwks_config.PUBLIC_PEM_DIRECTORY.exists):
            self._insure_path_deletion(self.jwks_config.PUBLIC_PEM_DIRECTORY)
            await asyncio.to_thread(
                self.jwks_config.PUBLIC_PEM_DIRECTORY.mkdir, parents=True
            )

        # Persist to PEM, and DB (JWKS done at end)
        await self.write_ecdsa_pair(
            private_key=sk,
            public_key=vk,
            key_id=int(active_kid),
        )

    async def get_jwks(self) -> list[dict[str, str]]:
        async with aiofiles.open(self.jwks_config.JWKS_FILEPATH, "rb") as jwks_file:
            return orjson.loads(await jwks_file.read())


@dataclass(slots=True)
class SyncedStoreKeyStateManager(AntiSingletonMixin):
    synced_store_client: Redis

    _valid_keys_view: list[str] = field(default_factory=list, init=False)

    @asynccontextmanager
    async def transactional_block(
        self,
    ) -> AsyncGenerator[None]:
        self._valid_keys_view = await self.synced_store_client.lrange(  # pyrefly: ignore[not-async]
            SyncedStoreStrings.VALID_KEYS, 0, -1
        )
        try:
            yield
        except Exception:
            async with self.synced_store_client.pipeline(transaction=True) as pipeline:
                pipeline.delete(SyncedStoreStrings.VALID_KEYS)
                pipeline.lpush(SyncedStoreStrings.VALID_KEYS, *self._valid_keys_view)
                await pipeline.execute()
            raise
        finally:
            self._valid_keys_view.clear()

    async def get_key_operational_cooldown(self) -> bool:
        return bool(
            await self.synced_store_client.get(SyncedStoreStrings.KEY_ROTATION_COOLDOWN)
        )

    async def get_valid_keys_ids(self) -> list[str]:
        return await self.synced_store_client.lrange(  # pyrefly: ignore[not-async]
            SyncedStoreStrings.VALID_KEYS, 0, -1
        )

    async def get_active_key_id(self) -> str:
        return (await self.get_valid_keys_ids())[0]

    async def overwrite_valid_keys(self, keys: Sequence[str]) -> None:
        async with self.synced_store_client.pipeline(transaction=True) as pipeline:
            pipeline.delete(SyncedStoreStrings.VALID_KEYS)
            pipeline.lpush(*keys)
            await pipeline.execute()


@dataclass(slots=True, frozen=True)
class KeyLifecycleManager(AntiSingletonMixin):
    synced_store_client: Redis
    keydata_repository: KeydataRepository
    token_manager: TokenManager
    filesystem_key_manager: FileSystemKeyManager
    synced_store_key_manager: SyncedStoreKeyStateManager

    _lock_value: str = field(default="LOCK", kw_only=True)
    _lock_prefix: str = field(default="LOCK", kw_only=True)
    _string_separator: str = field(default=GENERIC_SEPARATOR, kw_only=True)

    def generate_distributed_lock_name(self, operation: str) -> str:
        return self._string_separator.join((self._lock_prefix, operation))

    _transactional_stack_order: tuple[SupportsTransactionalBlocks] = field(
        init=False, default_factory=tuple
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_transactional_stack_order",
            (
                self.filesystem_key_manager,
                self.synced_store_key_manager,
            ),
        )

    @asynccontextmanager
    async def _transactional_block(
        self,
        *,
        operation: SyncedStoreStrings | None = None,
        lock_duration: LockTIme = LockTIme.LOW,
        operational_cooldown_duration: int | None = None,
    ) -> AsyncGenerator[None]:
        lock: Final[str | None] = (
            self.generate_distributed_lock_name(operation) if operation else None
        )
        if lock:
            _lock_acquired: bool = bool(
                await self.synced_store_client.set(
                    lock, self._lock_value, ex=lock_duration, nx=True
                )
            )
            if not _lock_acquired:
                raise Exception(f"Failed to acquire operational lock for {operation}")
        try:
            async with AsyncExitStack() as stack:
                for transactional_worker in self._transactional_stack_order:
                    await stack.enter_async_context(
                        transactional_worker.transactional_block()
                    )
                yield

                if operational_cooldown_duration is not None:
                    await self.synced_store_client.set(
                        SyncedStoreStrings.KEY_ROTATION_COOLDOWN,
                        1,
                        ex=operational_cooldown_duration,
                    )
        finally:
            if lock is not None:
                await self.synced_store_client.delete(lock)

    async def invalidate_key(
        self,
        key_id: str,
        *,
        intermediate_message_mapping: MutableMapping[str, str] | None = None,
        cooldown_duration: int | None = None,
    ) -> None:
        async with self._transactional_block(
            operation=SyncedStoreStrings.INVALIDATE_KEY,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            target_key: KeyPrivateDataResult | None = None
            async with self.keydata_repository.unit_of_work():
                # Select and lock key if exists
                # Weaker, shared FOR KEY SHARE lock acquired since contention is
                # handled above DB, and we only need to prevent DELETE and key-UPDATEs
                target_key = await self.keydata_repository.get_keydata(
                    key_id,
                    public_only=False,
                    lock_args=(SelectionLockOption.KEY_SHARE, SelectionLockOption.READ),
                )
                if not target_key:
                    raise ValueError(f"No key with ID {key_id} found")
                if not target_key.rotated_out_at:
                    raise Exception(f"Cannot invalidate active key {key_id}")
                if target_key.expired_at is not None:
                    raise Exception(f"Key {key_id} has already been expired")
                await self.keydata_repository.expire_keydata(key_id)

                # Before committing to DB, delete public PEM file, and update JWKS
                await self.filesystem_key_manager.invalidate_keys((target_key.kid,))

                # Key invalidation successful, update local token manager
                self.token_manager.invalidate_key(key_id)  # TODO: Add rollback control

                # Update distributed state
                valid_keys: list[
                    str
                ] = await self.synced_store_key_manager.get_valid_keys_ids()

                # Should never happen, but in case it does we fall back and regenerate the entire list
                if not valid_keys or key_id not in valid_keys:
                    if intermediate_message_mapping:
                        intermediate_message_mapping["keylist_integrity_warning"] = (
                            "Synced keylist state was inconsistent and hence regenerated through database"
                        )
                    valid_keys: list[str] = [
                        k.kid
                        for k in await self.keydata_repository.get_relevant_keydata(
                            None
                        )
                    ]
                else:
                    valid_keys.remove(key_id)

                await self.synced_store_key_manager.overwrite_valid_keys(valid_keys)

    async def clean_keystore(
        self, cooldown_duration: int | None = None
    ) -> tuple[str, tuple[str, ...]]:
        """Invalidate all keys except for the currently active key"""
        jwks_data = await self.filesystem_key_manager.get_jwks()
        if len(jwks_data) == 1:
            raise Exception("No active keys present to invalidate")
        async with self._transactional_block(
            operation=SyncedStoreStrings.INVALIDATE_KEY,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            async with self.keydata_repository.unit_of_work():
                valid_inactive_keys: list[
                    KeyPublicDataResult
                ] = await self.keydata_repository.get_valid_inactive_keys(
                    lock_args=(SelectionLockOption.KEY_SHARE, SelectionLockOption.READ)
                )
                await self.keydata_repository.batch_expire_keydata(
                    tuple(k.kid for k in valid_inactive_keys)
                )

                # Fetch latest KID to prune JWKS and PEM files accordingly
                active_key: (
                    KeyPublicDataResult | None
                ) = await self.keydata_repository.get_active_key()
                if not active_key:  # Violates business invariant, should never happen
                    raise Exception("Invalid keystore state!")

                await self.filesystem_key_manager.invalidate_keys(
                    tuple(
                        kid
                        for key_data in jwks_data
                        if (kid := key_data["kid"]) != active_key.kid
                    )
                )

                await self.synced_store_key_manager.overwrite_valid_keys(
                    (active_key.kid,)
                )

                for key in valid_inactive_keys:
                    self.token_manager.invalidate_key(key.kid)

        return active_key.kid, tuple(i.kid for i in valid_inactive_keys)

    async def rotate_key(
        self,
        cooldown_duration: int | None = None,
        *,
        rotation_author: int | None = None,
    ) -> KeyPublicDataResult:
        async with self._transactional_block(
            operation=SyncedStoreStrings.INVALIDATE_KEY,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            kid, signing_key, verification_key = (
                self.filesystem_key_manager.generate_ecdsa_pair()
            )
            generation_epoch: Final[datetime] = datetime.now(UTC)
            private_pem: Final[bytes] = signing_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.PKCS8,
                encryption_algorithm=NoEncryption(),
            )
            public_pem: Final[bytes] = verification_key.public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            )

            target_id: str | None = None
            async with self.keydata_repository.unit_of_work():
                # Update currently active key
                previous_key: (
                    KeyPublicDataResult | None
                ) = await self.keydata_repository.get_active_key(
                    lock_args=(SelectionLockOption.READ, SelectionLockOption.KEY_SHARE)
                )
                if not previous_key:
                    raise Exception("Invalid key state!")

                # Reflect rotation in DB
                new_key: Final[
                    KeyPublicDataResult
                ] = await self.keydata_repository.rotate_key(
                    previous_key.kid,
                    kid,
                    public_pem,
                    private_pem,
                    rotation_author=rotation_author,
                    epoch=generation_epoch,
                    returning=True,
                )

                # Check whether max capacity has been reached. If so, purge oldest key
                valid_inactive_key_data: list[tuple[str, datetime]] = [
                    (i.kid, i.rotated_out_at)
                    for i in (
                        await self.keydata_repository.get_valid_inactive_keys(
                            lock_args=(
                                SelectionLockOption.READ,
                                SelectionLockOption.KEY_SHARE,
                            )
                        )
                    )
                ]

                if (
                    len(valid_inactive_key_data)
                    > self.filesystem_key_manager.keys_config.MAX_VALID_KEYS
                ):
                    target_id = sorted(valid_inactive_key_data, key=lambda x: x[1])[0][
                        0
                    ]
                    await self.keydata_repository.expire_keydata(target_id)

                # Update files
                await self.filesystem_key_manager.update_jwks(
                    verification_key,
                    kid,
                )
                await self.filesystem_key_manager.write_ecdsa_pair(
                    private_key=signing_key,
                    public_key=verification_key,
                    key_id=kid,
                )

                # Remove previous key's private PEM file
                await self.filesystem_key_manager.delete_key_files(
                    (previous_key.kid,), delete_private=True, delete_public=False
                )
                if target_id is not None:
                    await self.filesystem_key_manager.delete_key_files(
                        (target_id,), delete_private=True
                    )

                # Update token manager's mapping to use this newly created ECDSA pair
                # TODO: Update TokenManager to accept DTO over this dataclass
                new_keydata: KeyMetadata = KeyMetadata(
                    PUBLIC_PEM=public_pem,
                    PRIVATE_PEM=private_pem,
                    ALGORITHM="ES256",
                )
                self.token_manager.update_keydata(kid, new_keydata)

                # Update distributed state
                valid_keys: list[
                    str
                ] = await self.synced_store_key_manager.get_valid_keys_ids()

                # Should never happen, but in case it does we fall back and regenerate the entire list
                if not valid_keys or kid not in valid_keys:
                    valid_keys: list[str] = [
                        k.kid
                        for k in await self.keydata_repository.get_relevant_keydata(
                            None
                        )
                    ]
                elif target_id in valid_keys:
                    # Remove invalidated key ID
                    # in this branch, target_id will always be str since valid_keys is always Sequence[str]
                    valid_keys.remove(target_id)  # pyrefly: ignore[bad-argument-type]

                # At this state, valid_keys is a consistent list of key IDs
                # Set global cooldown for key rotation, update global state, and release rotation lock
                await self.synced_store_key_manager.overwrite_valid_keys(valid_keys)
        return new_key
