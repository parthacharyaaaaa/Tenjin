import asyncio
from dataclasses import dataclass, field
from traceback import format_exc
from typing import Final

import ecdsa
import httpx

from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from auxillary.utils import from_base64url

from resource_server.config.app_config import AppConfig
from resource_server.config.constants import RedisConstants
from auxillary.singleton import SingletonMetaclass
from resource_server.utils.typing import JWKSEntry


@dataclass(slots=True, weakref_slot=True)
class KeyManager(metaclass=SingletonMetaclass):
    app_config: Final[AppConfig]
    app_redis_client: Final[Redis]
    auth_redis_client: Final[Redis]
    current_mapping: dict[str, bytes] = field(default_factory=dict)
    _pubsub: PubSub | None = field(default=None)
    _monitoring_task: asyncio.Task | None = field(init=False, default=None)
    _jwks_endpoint: str = ""

    def __post_init__(self) -> None:
        self._jwks_endpoint = "http://" + "/".join(
            (self.app_config.CORE.AUTH_SERVER_NAME, self.app_config.JWKS.JWKS_ENDPOINT)
        )

    @staticmethod
    def parse_keyset(
        raw_keyset: str, entry_delimitor: str, pair_delimitor: str
    ) -> dict[str, bytes]:
        return {
            entry.split(pair_delimitor)[0]: entry.split(pair_delimitor)[1].encode(
                "utf-8"
            )
            for entry in raw_keyset.split(entry_delimitor)
        }

    async def subscribe(self):
        self._pubsub = self.auth_redis_client.pubsub()
        await self._pubsub.subscribe(self.app_config.JWKS.KEY_ANNOUNCEMENT_AUTH_CHANNEL)

    async def sync_jwks(self) -> None:
        await self.subscribe()
        if not self._pubsub:
            raise TypeError("PubSub object not instantiated")

        async for message in self._pubsub.listen():
            keys: dict[str, bytes] = self.parse_keyset(message, ":", "=")
            expired_keys: tuple[str, ...] = tuple(
                k for k in keys.keys() if k not in self.current_mapping
            )
            for key_id in expired_keys:
                self.current_mapping.pop(key_id)
                keys.pop(key_id)

            for key_id, key in keys.items():
                self.current_mapping.setdefault(key_id, key)

    def start_jwks_monitoring(self) -> None:
        if not self._monitoring_task:
            self._monitoring_task = asyncio.create_task(
                self.sync_jwks(), name=f"{self}:{self.start_jwks_monitoring.__name__}"
            )

    async def stop_jwks_monitoring(self) -> None:
        if not self._monitoring_task:
            return

        self._monitoring_task.cancel()
        try:
            await self._monitoring_task
        except asyncio.CancelledError:
            pass
        self._monitoring_task = None

    @property
    def jwks_endpoint(self) -> str:
        return self._jwks_endpoint

    async def get_global_key_mapping(self) -> dict[str, bytes]:
        """Get JWKS cache in Redis"""
        res: dict[str, str] = await self.app_redis_client.hgetall(
            RedisConstants.JWKS_MAPPING
        )  # type: ignore[reportGeneralTypeIssues]

        # crypto APIs expect bytes
        return {kid: pub_pem.encode() for kid, pub_pem in res.items()}

    async def get_jwks(self) -> list[JWKSEntry] | None:
        """Read and return JWKS from source"""
        async with httpx.AsyncClient() as client:
            response: httpx.Response = await client.get(
                self.jwks_endpoint,
                timeout=self.app_config.JWKS.JWKS_REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return None

        new_mapping: list[JWKSEntry] = response.json().get("keys")
        if not new_mapping:
            pass
        # TODO: Ping auth server to indicate malformatted JWKS response
        return new_mapping

    async def update_jwks(self) -> None:
        """Fetch JWKS from auth server and load any new key mappings into current_mapping"""
        res: int = await self.app_redis_client.set(
            RedisConstants.JWKS_POLL_LOCK,
            1,
            ex=self.app_config.JWKS.UPDATION_LOCK_LIFESPAN,
            nx=True,
        )

        # Wait for current worker and then read global key mapping
        if not res:
            for i in range(self.app_config.JWKS.MAX_GLOBAL_MAPPING_POLLS):
                if await self.app_redis_client.get(RedisConstants.JWKS_POLL_LOCK):
                    await asyncio.sleep(
                        self.app_config.JWKS.GLOBAL_MAPPING_POLL_INTERVAL * 2
                    )
                break

            global_mapping: dict[str, bytes] = {}
            for t in range(self.app_config.JWKS.MAX_GLOBAL_MAPPING_POLLS):
                global_mapping = await self.get_global_key_mapping()
                if global_mapping:
                    self.current_mapping = global_mapping
                    return

            raise RuntimeError("Failed to concile JWKS")
        try:
            new_mapping: list[JWKSEntry] | None = await self.get_jwks()

            if not new_mapping:
                # TODO: Improved handling of JWKS failures
                return

            local_keys: frozenset[str] = frozenset(self.current_mapping.keys())
            global_valid_keys: frozenset[str] = frozenset(
                mapping["kid"] for mapping in new_mapping
            )

            # Purge local keys that are invalid
            for expired_key in local_keys - global_valid_keys:
                self.current_mapping.pop(expired_key)

            for keyMetadata in new_mapping:
                # New key found, welcome to the club >:3
                if keyMetadata["kid"] not in self.current_mapping:
                    x = from_base64url(keyMetadata["x"])
                    y = from_base64url(keyMetadata["y"])
                    point = ecdsa.ellipticcurve.Point(ecdsa.SECP256k1.curve, x, y)  # type: ignore[reportAttributeAccessIssue]
                    vk = ecdsa.VerifyingKey.from_public_point(
                        point, curve=ecdsa.SECP256k1
                    )

                    self.current_mapping[keyMetadata["kid"]] = vk.to_pem()

            # Update global list and values in Redis to inform other workers
            async with self.app_redis_client.pipeline() as pipe:
                # Overwrite mapping entirely
                pipe.delete(RedisConstants.JWKS_MAPPING)
                pipe.hset(RedisConstants.JWKS_MAPPING, mapping=self.current_mapping)
                await pipe.execute()

        except Exception:
            print(format_exc())
        finally:
            async with self.app_redis_client.pipeline() as pipe:
                pipe.delete(RedisConstants.JWKS_POLL_LOCK)
                pipe.set(
                    RedisConstants.JWKS_POLL_COOLDOWN,
                    value=1,
                    ex=self.app_config.JWKS.JWKS_POLL_INTERVAL,
                )
                await pipe.execute()
