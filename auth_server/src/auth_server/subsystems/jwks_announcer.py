from dataclasses import dataclass

import orjson
from auxillary.security.data_structures.jwks import VariableJWKS
from auxillary.singleton import SingletonMetaclass
from redis.asyncio.client import Redis

from auth_server.config.sub_config import JWKSConfigModel


@dataclass(slots=True, frozen=True, weakref_slot=True)
class JWKSUpdateAnnouncer(metaclass=SingletonMetaclass):
    redis_client: Redis
    jwks_config: JWKSConfigModel

    async def stream_update(self, jwks: VariableJWKS) -> None:
        await self.redis_client.xadd(
            self.jwks_config.UPDATION_STREAM_NAME,
            fields={"keys": orjson.dumps(jwks.model_dump(mode="json"))},
        )
