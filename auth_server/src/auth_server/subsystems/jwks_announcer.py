from dataclasses import dataclass

from auxillary.security.data_structures.jwks.typing import JWKS
from auxillary.singleton import SingletonMetaclass
from redis.asyncio.client import Redis

from auth_server.config.sub_config import JWKSConfigModel


@dataclass(slots=True, frozen=True, weakref_slot=True)
class JWKSUpdateAnnouncer(metaclass=SingletonMetaclass):
    redis_client: Redis
    jwks_config: JWKSConfigModel

    async def stream_update(self, jwks: JWKS) -> None:
        await self.redis_client.xadd(
            self.jwks_config.UPDATION_STREAM_NAME,
            fields={"jwks": jwks.model_dump_json()},
        )
