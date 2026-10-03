from collections.abc import AsyncGenerator, MutableMapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, IntFlag
from typing import Final, Protocol

from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.mixins.metaclass import AntiSingletonMixin
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from redis.asyncio.client import Redis

from auth_server.config.sub_config import KeyConfigModel
from auth_server.keys.keygen import generate_ecdsa_pair
from auth_server.repositories.keydata import (
    EllipticCurveJWKSResult,
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.strings import (
    GENERIC_SEPARATOR,
    SelectionLockOption,
    SyncedStoreKeyStrings,
)
from auth_server.tokens.token_manager import TokenManager


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


@dataclass(slots=True)
class SyncedStoreKeyStateManager(AntiSingletonMixin):
    synced_store_client: Redis

    _valid_keys_view: list[str] = field(default_factory=list, init=False)

    @asynccontextmanager
    async def transactional_block(
        self,
    ) -> AsyncGenerator[None]:
        self._valid_keys_view = await self.synced_store_client.lrange(  # pyrefly: ignore[not-async]
            SyncedStoreKeyStrings.VALID_KEYS, 0, -1
        )
        try:
            yield
        except Exception:
            async with self.synced_store_client.pipeline(transaction=True) as pipeline:
                pipeline.delete(SyncedStoreKeyStrings.VALID_KEYS)
                pipeline.lpush(SyncedStoreKeyStrings.VALID_KEYS, *self._valid_keys_view)
                await pipeline.execute()
            raise
        finally:
            self._valid_keys_view.clear()

    async def get_key_operational_cooldown(self) -> bool:
        return bool(
            await self.synced_store_client.get(
                SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN
            )
        )

    async def get_valid_keys_ids(self) -> list[str]:
        return await self.synced_store_client.lrange(  # pyrefly: ignore[not-async]
            SyncedStoreKeyStrings.VALID_KEYS, 0, -1
        )

    async def get_active_key_id(self) -> str:
        return (await self.get_valid_keys_ids())[0]

    async def overwrite_valid_keys(self, keys: Sequence[str]) -> None:
        async with self.synced_store_client.pipeline(transaction=True) as pipeline:
            pipeline.delete(SyncedStoreKeyStrings.VALID_KEYS)
            pipeline.lpush(*keys)
            await pipeline.execute()

    async def set_operation_cooldown(self, cooldown: timedelta | int) -> None:
        await self.synced_store_client.set(
            SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN,
            1,
            ex=cooldown,
        )


@dataclass(slots=True, frozen=True)
class KeyLifecycleManager(AntiSingletonMixin):
    keydata_repository: KeydataRepository
    token_manager: TokenManager
    key_config: KeyConfigModel
    synced_store_key_manager: SyncedStoreKeyStateManager
    redis_lock_factory: RedisInstanceLockFactory

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
                self.synced_store_key_manager,
                self.token_manager,
            ),
        )

    @asynccontextmanager
    async def _transactional_block(
        self,
        *,
        operation: SyncedStoreKeyStrings | None = None,
        lock_duration: LockTIme = LockTIme.LOW,
        operational_cooldown_duration: int | timedelta | None = None,
    ) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            if operation:
                lock_name: Final[str] = self.generate_distributed_lock_name(operation)
                lock_set = await stack.enter_async_context(
                    await self.redis_lock_factory.lock(
                        lock_name, self._lock_value, lock_duration
                    )
                )
                if not lock_set.valid:
                    raise Exception(
                        f"Failed to acquire operational lock for {operation}"
                    )
            for transactional_worker in self._transactional_stack_order:
                await stack.enter_async_context(
                    transactional_worker.transactional_block()
                )
            yield

            if operational_cooldown_duration:
                await self.synced_store_key_manager.set_operation_cooldown(
                    operational_cooldown_duration
                )

    async def invalidate_key(
        self,
        key_id: str,
        *,
        intermediate_message_mapping: MutableMapping[str, str] | None = None,
        cooldown_duration: int | None = None,
    ) -> None:
        async with self._transactional_block(
            operation=SyncedStoreKeyStrings.INVALIDATE_KEY,
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

                # Before committing to DB, and update JWKS
                # await self.filesystem_key_manager.invalidate_keys((target_key.kid,))

                # Key invalidation successful, update local token manager
                self.token_manager.invalidate_key(key_id)

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
        async with self._transactional_block(
            operation=SyncedStoreKeyStrings.INVALIDATE_KEY,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            async with self.keydata_repository.unit_of_work():
                jwks_data: list[EllipticCurveJWKSResult] = list(
                    await self.keydata_repository.get_jwks()
                )
                if len(jwks_data) == 1:
                    raise Exception("No inactive keys present to invalidate")
                active_key: Final[EllipticCurveJWKSResult] = jwks_data.pop(0)

                # Prune JWKS
                await self.keydata_repository.batch_expire_keydata(
                    tuple(k.kid for k in jwks_data)
                )
                await self.synced_store_key_manager.overwrite_valid_keys(
                    (active_key.kid,)
                )
                for key in jwks_data:
                    self.token_manager.invalidate_key(key.kid)

        return active_key.kid, tuple(i.kid for i in jwks_data)

    async def rotate_key(
        self,
        cooldown_duration: int | None = None,
        *,
        rotation_author: int | None = None,
    ) -> KeyPublicDataResult:
        async with self._transactional_block(
            operation=SyncedStoreKeyStrings.INVALIDATE_KEY,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            kid, signing_key, verification_key = generate_ecdsa_pair(self.key_config)
            generation_epoch: Final[datetime] = datetime.now(UTC)
            private_pem: Final[bytes] = pem_serialize_private_key(
                signing_key, self.key_config.PRIVATE_PEM_FORMAT
            )
            public_pem: Final[bytes] = pem_serialize_public_key(
                verification_key,
                self.key_config.PUBLIC_PEM_FORMAT,
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
                    KeyPrivateDataResult
                ] = await self.keydata_repository.rotate_key(
                    previous_key.kid,
                    kid,
                    public_pem,
                    private_pem,
                    rotation_author=rotation_author,
                    epoch=generation_epoch,
                    returning=True,
                    public_only=False,
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

                if len(valid_inactive_key_data) > self.key_config.MAX_VALID_KEYS:
                    target_id = sorted(valid_inactive_key_data, key=lambda x: x[1])[0][
                        0
                    ]
                    await self.keydata_repository.expire_keydata(target_id)

                # Update token manager's mapping to use this newly created ECDSA pair
                self.token_manager.update_keydata(kid, new_key)

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
        return new_key.create_public_copy()
