import asyncio
from collections.abc import AsyncGenerator, MutableMapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, IntFlag
from logging import Logger, getLogger
from traceback import format_exc
from typing import Final, Protocol

import orjson
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.mixins.metaclass import AntiSingletonMixin
from auxillary.security.data_structures.jwks.models import (
    EllipticCurveJWK,
    VariableJWKS,
)
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from auxillary.utils import json_repr
from redis.asyncio.client import Redis

from auth_server.config.sub_config import JWKSConfigModel, KeyConfigModel
from auth_server.datastructures.exceptions.keys import (
    EmptyKeysError,
    ExpiredKeyError,
    InvalidKeyOperationError,
    KeyNotFoundError,
    KeyStateError,
)
from auth_server.keys.keygen import generate_ecdsa_pair
from auth_server.models.data_transport.keydata import (
    ActiveKeyPublicDataResult,
    ExpiredKeyDataResult,
    RotatedKeyDataResult,
)
from auth_server.repositories.keydata import (
    EllipticCurveJWKResult,
    KeydataRepository,
)
from auth_server.strings import (
    GENERIC_SEPARATOR,
    SelectionLockOption,
    SyncedStoreKeyStrings,
)
from auth_server.subsystems.jwks_announcer import JWKSUpdateAnnouncer
from auth_server.subsystems.token_manager import TokenManager


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

    @asynccontextmanager
    async def transactional_block(
        self,
    ) -> AsyncGenerator[None]:
        raw_jwks: Final[str] = await self.synced_store_client.get(
            SyncedStoreKeyStrings.JWKS_KEY
        )
        try:
            yield
        except Exception:
            async with self.synced_store_client.pipeline(transaction=True) as pipeline:
                pipeline.set(SyncedStoreKeyStrings.JWKS_KEY, raw_jwks)
                await pipeline.execute()
            raise

    async def get_key_operational_cooldown(self) -> bool:
        return bool(
            await self.synced_store_client.get(
                SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN
            )
        )

    async def set_operation_cooldown(self, cooldown: timedelta | int) -> None:
        await self.synced_store_client.set(
            SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN,
            1,
            ex=cooldown,
        )

    async def get_jwks(self) -> list[EllipticCurveJWKResult]:
        raw_jwks: str = await self.synced_store_client.get(
            SyncedStoreKeyStrings.JWKS_KEY
        )
        return [
            EllipticCurveJWKResult.initialize_from_cache(k)
            for k in orjson.loads(raw_jwks)["keys"]
        ]

    async def set_jwks(self, keys: Sequence[EllipticCurveJWKResult]) -> None:
        if not keys:
            raise EmptyKeysError("keys must be non-empty")
        await self.synced_store_client.set(
            SyncedStoreKeyStrings.JWKS_KEY,
            orjson.dumps({"keys": [json_repr(k) for k in keys]}),
        )


@dataclass(slots=True, frozen=True)
class KeyLifecycleManager(AntiSingletonMixin):
    keydata_repository: KeydataRepository
    token_manager: TokenManager
    key_config: KeyConfigModel
    jwks_config: JWKSConfigModel
    synced_store_key_manager: SyncedStoreKeyStateManager
    redis_lock_factory: RedisInstanceLockFactory
    jwks_update_announcer: JWKSUpdateAnnouncer

    _lock_value: str = field(default="LOCK", kw_only=True)
    _lock_prefix: str = field(default="LOCK", kw_only=True)
    _string_separator: str = field(default=GENERIC_SEPARATOR, kw_only=True)
    _logger: Logger = field(
        default_factory=lambda: getLogger(name="key_lifecycle_manager_logger")
    )
    _token_manager_polling_task: asyncio.Task[None] = field(init=False)

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

    def start_token_manager_polling(self) -> None:
        _token_manager_polling_task: Final[asyncio.Task[None]] = asyncio.create_task(
            self.token_manager_updation_poller(
                self.token_manager.token_manager_config.POLL_INTERVAL
            )
        )
        object.__setattr__(
            self, "_token_manager_polling_task", _token_manager_polling_task
        )

    async def stop_token_manager_polling(self) -> None:
        self._token_manager_polling_task.cancel()
        try:
            await self._token_manager_polling_task
        except asyncio.CancelledError:
            pass
        object.__setattr__(self, "_token_manager_polling_task", None)

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
                        lock_name, self._lock_value, lock_duration.value
                    )
                )
                if not lock_set.valid:
                    raise RuntimeError(
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
            operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            async with self.keydata_repository.unit_of_work():
                # Select and lock key if exists
                # Weaker, shared FOR KEY SHARE lock acquired since contention is
                # handled above DB, and we only need to prevent DELETE and key-UPDATEs
                target_key: (
                    RotatedKeyDataResult | ExpiredKeyDataResult | None
                ) = await self.keydata_repository.get_rotated_keydata(
                    key_id,
                    include_expired=True,
                    lock_args=(SelectionLockOption.KEY_SHARE, SelectionLockOption.READ),
                )
                if not target_key:
                    raise KeyNotFoundError(f"No inactive key with ID {key_id} found")
                if isinstance(target_key, ExpiredKeyDataResult):
                    raise ExpiredKeyError(
                        f"Key {key_id} already expired at {target_key.expired_at.isoformat()}"
                    )
                await self.keydata_repository.expire_keydata(key_id)

                # Key invalidation successful, update local token manager
                self.token_manager.invalidate_key(key_id)
                # Update distributed state
                jwks: list[
                    EllipticCurveJWKResult
                ] = await self.synced_store_key_manager.get_jwks()

                # Should never happen, but in case it does we fall back and regenerate the entire list
                if not (jwks and any(j.kid == key_id for j in jwks)):
                    if intermediate_message_mapping is not None:
                        intermediate_message_mapping["keylist_integrity_warning"] = (
                            "Synced keylist state was inconsistent and hence regenerated through database"
                        )
                    jwks = list(await self.keydata_repository.get_jwks())
                else:
                    jwks = list(filter(lambda x: x.kid != key_id, jwks))

                await self.synced_store_key_manager.set_jwks(jwks)

        await self.jwks_update_announcer.stream_update(
            VariableJWKS.model_validate(
                {"keys": [EllipticCurveJWK.model_validate(asdict(jwk)) for jwk in jwks]}
            )
        )

    async def clean_keystore(
        self, cooldown_duration: int | None = None
    ) -> tuple[str, tuple[str, ...]]:
        """Invalidate all keys except for the currently active key"""
        async with self._transactional_block(
            operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
            lock_duration=LockTIme.MEDIUM,
            operational_cooldown_duration=cooldown_duration,
        ):
            async with self.keydata_repository.unit_of_work():
                jwks_data: list[EllipticCurveJWKResult] = list(
                    await self.keydata_repository.get_jwks()
                )
                if len(jwks_data) == 1:
                    raise InvalidKeyOperationError(
                        "No inactive keys present to invalidate"
                    )
                active_key: Final[EllipticCurveJWKResult] = jwks_data.pop(0)
                # Prune JWKS
                await self.keydata_repository.batch_expire_keydata(
                    tuple(k.kid for k in jwks_data)
                )
                await self.synced_store_key_manager.set_jwks((active_key,))
                for key in jwks_data:
                    self.token_manager.invalidate_key(key.kid)

        await self.jwks_update_announcer.stream_update(
            VariableJWKS.model_validate(
                {"keys": [EllipticCurveJWK.model_validate(asdict(active_key))]}
            )
        )

        return active_key.kid, tuple(i.kid for i in jwks_data)

    async def rotate_key(
        self,
        cooldown_duration: int | None = None,
        *,
        rotation_author: int | None = None,
    ) -> ActiveKeyPublicDataResult:
        async with self._transactional_block(
            operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
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

            async with self.keydata_repository.unit_of_work():
                # Update currently active key
                previous_key: (
                    ActiveKeyPublicDataResult | None
                ) = await self.keydata_repository.get_active_key(
                    lock_args=(SelectionLockOption.READ, SelectionLockOption.KEY_SHARE)
                )
                if not previous_key:  # Should never happen!!!
                    raise KeyStateError("Invalid key state!")

                # Reflect rotation in DB
                new_key: Final[
                    ActiveKeyPublicDataResult
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
                valid_inactive_key_data: list[tuple[str, datetime]] = sorted(
                    [
                        (i.kid, i.rotated_out_at)
                        for i in (
                            await self.keydata_repository.get_valid_inactive_keys(
                                lock_args=(
                                    SelectionLockOption.READ,
                                    SelectionLockOption.KEY_SHARE,
                                )
                            )
                        )
                    ],
                    key=lambda x: x[1],
                )

                if len(valid_inactive_key_data) > self.jwks_config.JWKS_CAP:
                    await self.keydata_repository.batch_expire_keydata(
                        tuple(
                            i[0]
                            for i in valid_inactive_key_data[
                                : -self.jwks_config.JWKS_CAP
                            ]
                        )
                    )

                # Update token manager's mapping to use this newly created ECDSA pair
                self.token_manager.update_keydata(kid, new_key)
                # Update distributed state
                jwks: list[EllipticCurveJWKResult] = list(
                    await self.keydata_repository.get_jwks()
                )
                await self.synced_store_key_manager.set_jwks(jwks)
        await self.jwks_update_announcer.stream_update(
            VariableJWKS.model_validate(
                {"keys": [EllipticCurveJWK.model_validate(asdict(jwk)) for jwk in jwks]}
            )
        )
        return new_key

    async def token_manager_updation_poller(self, interval: timedelta) -> None:
        while True:
            try:
                global_jwks: Final[tuple[EllipticCurveJWKResult, ...]] = tuple(
                    await self.synced_store_key_manager.get_jwks()
                )
                if not global_jwks:
                    raise KeyStateError("Global key state empty or not found")
                await self.token_manager.update_local_store(global_jwks)
            except Exception:
                self._logger.error("Exception encountered. Traceback:")
                self._logger.error(format_exc())
            finally:
                await asyncio.sleep(interval.total_seconds())
