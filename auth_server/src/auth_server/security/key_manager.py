import asyncio
import secrets
from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
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
from auth_server.repositories.keydata import KeyPrivateDataResult
from auth_server.security.token_manager import TokenManager
from auth_server.strings import GENERIC_SEPARATOR, SyncedStoreStrings


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
        kid: Final[str] = secrets.token_hex(self.keys_config.KEY_IDENTIFIER_LENGTH)
        return kid, private_key, public_key

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
                    "kid": key.kid,
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
        kid: str,
        enforce_capacity: bool = True,
    ) -> None:
        """Updates the JWKS JSON file to include the given public key as the latest key"""
        public_numbers: ec.EllipticCurvePublicNumbers = vk.public_numbers()
        encoded_x, encoded_y = (
            to_base64url(public_numbers.x),
            to_base64url(public_numbers.y),
        )
        key_mapping: dict[str, str | int] = {
            "kty": "EC",
            "alg": "ECDSA",
            "crv": ec.SECP256K1.name,
            "use": "sig",
            "kid": kid,
            "x": encoded_x,
            "y": encoded_y,
        }

        async with aiofiles.open(
            self.jwks_config.JWKS_FILEPATH, "r+"
        ) as jwks_json_file:
            jwks_contents: list[dict[str, str | int]] = orjson.loads(
                await jwks_json_file.read()
            )["keys"]
            jwks_contents.append(key_mapping)
            length: int = len(jwks_contents)

            if enforce_capacity and length > self.keys_config.MAX_VALID_KEYS:
                jwks_contents: list[dict[str, str | int]] = jwks_contents[
                    -self.keys_config.MAX_VALID_KEYS :
                ]

            await jwks_json_file.truncate(0)
            await jwks_json_file.seek(0)
            await jwks_json_file.write(
                orjson.dumps({"keys": jwks_contents}).decode("utf-8")
            )

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


@dataclass(slots=True, frozen=True)
class KeyLifecycleManager(AntiSingletonMixin):
    synced_store_client: Redis
    synced_store_key_manager: SyncedStoreKeyStateManager
    filesystem_key_manager: FileSystemKeyManager
    token_manager: TokenManager

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
