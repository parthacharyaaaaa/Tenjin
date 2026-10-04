import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import uuid4

import httpx
import orjson
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.security.data_structures.jwks import (
    EllipticCurveJWK,
    EllipticCurveJWKS,
)
from auxillary.security.data_structures.jwks_enums import JWKKty
from auxillary.security.serialization import pem_serialize_public_key
from auxillary.singleton import SingletonMetaclass
from cryptography.hazmat.primitives.asymmetric import ec
from redis.asyncio import Redis
from resource_auxillary.coordination import exponential_jittered_backoff

from resource_server.config.constants import RedisConstants
from resource_server.config.sub_config import JWKSConfig
from resource_server.utils.typing import JWKSEntry


@dataclass(slots=True, weakref_slot=True, frozen=True)
class KeyManager(metaclass=SingletonMetaclass):
    auth_server_address: str
    jwks_config: JWKSConfig
    app_redis_client: Redis
    auth_redis_client: Redis
    distributed_lock_factory: RedisInstanceLockFactory
    _current_mapping: dict[str, bytes] = field(default_factory=dict)
    consumer_name: str = field(
        kw_only=True, default_factory=lambda: f"{os.getpid}:{uuid4().hex}"
    )

    jwks_endpoint: str = field(init=False)
    _jwks_monitoring_task: asyncio.Task = field(init=False)
    _global_mapping_monitoring_task: asyncio.Task = field(init=False)
    _last_update_timestamp: int = field(init=False, default=-1)
    _casting_map: MappingProxyType[JWKKty, Callable] = field(
        init=False, default=MappingProxyType({JWKKty.EC: EllipticCurveJWK})
    )

    @property
    def current_mapping(self) -> dict[str, bytes]:
        return self._current_mapping

    def update_updation_timestamp(self, timestamp: int) -> None:
        if timestamp < self._last_update_timestamp:
            raise ValueError("New timestamp value lower than current timestamp")
        object.__setattr__(self, "_last_update_timestamp", timestamp)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "jwks_endpoint",
            "http://"
            + "/".join((self.auth_server_address, self.jwks_config.JWKS_ENDPOINT)),
        )

    def start_monitoring_tasks(self) -> None:
        for task, coro in {
            "_global_mapping_monitoring_task": self.global_mapping_poller,
            "_jwks_monitoring_task": self.jwks_updation_listener,
        }.items():
            task_ref: asyncio.Task[None] | None = getattr(self, task, None)
            if isinstance(task_ref, asyncio.Task):
                continue
            object.__setattr__(self, task, coro())

    async def stop_monitoring_tasks(self) -> None:
        for task in ("_global_mapping_monitoring_task", "_jwks_monitoring_task"):
            task_ref: asyncio.Task[None] | None = getattr(self, task, None)
            if not task_ref:
                continue
            task_ref.cancel()
            try:
                await task_ref
            except asyncio.CancelledError:
                pass
            object.__setattr__(self, task, None)

    async def fetch_global_key_mapping(self) -> dict[str, bytes]:
        """Get JWKS cache in Redis"""
        res: dict[str, str] = await self.app_redis_client.hgetall(  # pyrefly: ignore[not-async]
            RedisConstants.JWKS_MAPPING
        )
        return {kid: pub_pem.encode("utf-8") for kid, pub_pem in res.items()}

    async def fetch_jwks(self) -> EllipticCurveJWKS:
        """Read and return JWKS from issuing authority (auth server)"""
        async with httpx.AsyncClient() as client:
            response: httpx.Response = await client.get(
                self.jwks_endpoint,
                timeout=self.jwks_config.JWKS_REQUEST_TIMEOUT.total_seconds(),
            )
        response.raise_for_status()
        new_mapping: list[JWKSEntry] = response.json().get("keys")
        if not new_mapping:
            raise ValueError("Fetched JWKS data empty")
        # TODO: Ping auth server to indicate malformatted JWKS response
        return EllipticCurveJWKS.construct_from_jwks_list(new_mapping)

    def _update_local_mapping_from_jwks(self, fetched_jwks: EllipticCurveJWKS) -> None:
        local_keys: frozenset[str] = frozenset(self._current_mapping.keys())
        global_valid_keys: frozenset[str] = frozenset(
            jwk.kid for jwk in fetched_jwks.keys
        )
        for expired_key in local_keys - global_valid_keys:
            self._current_mapping.pop(expired_key)
        for jwk in fetched_jwks.keys:
            if jwk.kid in local_keys:
                continue
            # Welcome to the club >:3
            public_numbers = ec.EllipticCurvePublicNumbers(
                jwk.public_members.x, jwk.public_members.y, ec.SECP256K1()
            )
            verification_key = public_numbers.public_key()
            self._current_mapping[jwk.kid] = pem_serialize_public_key(
                verification_key, self.jwks_config.LOCAL_PUBKEY_ENCODING
            )

    def _local_reflect_global_mapping(
        self, global_mapping: Mapping[str, bytes]
    ) -> None:
        local_keys = self._current_mapping.keys()
        for expired_key in local_keys - global_mapping.keys():
            self._current_mapping.pop(expired_key)
        for key_id, public_pem_bytes in global_mapping.items():
            if key_id in local_keys:
                continue
            # Welcome to the club >:3
            self._current_mapping[key_id] = public_pem_bytes

    async def _global_reflect_local_changes(self) -> None:
        async with self.app_redis_client.pipeline(transaction=True) as pipe:
            pipe.delete(RedisConstants.JWKS_MAPPING)
            pipe.hset(RedisConstants.JWKS_MAPPING, mapping=self._current_mapping)
            await pipe.execute()

    async def hard_update_jwks(self) -> None:
        """
        Fetch JWKS from auth server
        and load any new key mappings into current_mapping
        """
        lock_context = await self.distributed_lock_factory.lock(
            RedisConstants.JWKS_POLL_LOCK,
            "__lock__",
            int(self.jwks_config.UPDATION_LOCK_LIFESPAN.total_seconds()),
        )
        if not lock_context:
            # Let background polling update local key data naturally
            return
        async with lock_context:
            fetched_jwks: EllipticCurveJWKS = await self.fetch_jwks()
            self._update_local_mapping_from_jwks(fetched_jwks)
            await self._global_reflect_local_changes()

    async def handle_invalid_updates(self, message_id: str) -> None:
        pass

    async def update_local_mapping(self) -> None:
        global_mapping: dict[str, bytes] = await self.fetch_global_key_mapping()
        self._local_reflect_global_mapping(global_mapping)

    async def _jwks_updation_stream_iterator(self):
        while True:
            try:
                update_data: list[
                    list[list[tuple[str, dict[str, str]]]]
                ] = await self.auth_redis_client.xreadgroup(
                    groupname=self.jwks_config.JWKS_UPDATE_LISTENER_GROUP_NAME,
                    consumername=self.consumer_name,
                    streams={self.jwks_config.JWKS_UPDATE_STREAM_NAME: ">"},
                    block=0,
                    count=1,
                )
                if (
                    event_id := int(update_data[0][1][0][0])
                ) < self._last_update_timestamp:
                    await self.auth_redis_client.xack(
                        self.jwks_config.JWKS_UPDATE_STREAM_NAME,
                        self.consumer_name,
                        event_id,
                    )
                    continue
                # Thank you redis python client for the amazing typing support :D
                yield (
                    event_id,
                    EllipticCurveJWKS.construct_from_jwks_list(
                        orjson.loads(update_data[0][1][0][1]["keys"])
                    ),
                )
            except Exception:  # nosec
                continue

    async def jwks_updation_listener(self) -> None:
        async for event_id, announced_jwks in self._jwks_updation_stream_iterator():
            # Invalidation is inherently idempotent
            self._update_local_mapping_from_jwks(announced_jwks)
            self.update_updation_timestamp(event_id)
            await self._global_reflect_local_changes()

    async def global_mapping_poller(self) -> None:
        while True:
            await self.update_local_mapping()
            await exponential_jittered_backoff(
                self.jwks_config.MAX_GLOBAL_MAPPING_POLL_INTERVAL,
                self.jwks_config.MIN_GLOBAL_MAPPING_POLL_INTERVAL,
                1,
            )
