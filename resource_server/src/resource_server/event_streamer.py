from dataclasses import dataclass
from functools import cached_property
from typing import Mapping, ClassVar

from redis.asyncio.client import Redis, Pipeline
from redis.typing import EncodableT, FieldT

from resource_server.config.sub_config import CacheConfig
from resource_server.utils.singleton import SingletonMetaclass

from resource_auxillary.strings import IntentFlag, StreamName, EventNames, Action
from resource_auxillary.cache import (
    derive_deletion_intent_flag,
    create_creation_intent_flag,
    derive_hashmap_name,
)


@dataclass(slots=True, weakref_slot=True)
class EventStreamer(metaclass=SingletonMetaclass):

    redis_client: Redis
    cache_config: CacheConfig

    DELETION_ACTIONS: ClassVar[frozenset[Action]] = frozenset(
        (Action.UNVOTE, Action.UNSUB)
    )

    @cached_property
    def allowed_intents(self) -> frozenset[str]:
        return frozenset(
            (
                IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG,
                IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
            )
        )

    def _pipeline_update_counter(
        self, pipeline: Pipeline, hashmap_name: str, identifier: str, delta: int
    ) -> None:
        """
        Update the global counter for a resource's field
        """
        pipeline.hincrby(hashmap_name, identifier, delta)

    def _pipeline_set_intent(self, pipeline: Pipeline, intent: str, ttl: int) -> None:
        if intent not in self.allowed_intents:
            raise ValueError(
                " ".join(
                    (
                        "Intent not allowed, must be one of:",
                        ", ".join(self.allowed_intents),
                    )
                )
            )
        pipeline.set(intent, 1, ex=ttl)

    def _pipeline_create_event(
        self,
        pipeline: Pipeline,
        stream: StreamName,
        event: EventNames,
        mapping: dict[FieldT, EncodableT],
    ) -> None:
        pipeline.xadd(stream.value, mapping | {"event": event.value}, nomkstream=False)

    async def emit_user_counter_event(
        self,
        event: EventNames,
        resource_name: str,
        resource_identifier: str | int,
        action: Action,
        flag: IntentFlag,
        user_identifier: str | int,
        event_body: Mapping[FieldT, EncodableT],
        delta: int,
    ):
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            self._pipeline_update_counter(
                pipeline,
                derive_hashmap_name(resource_name, action),
                str(resource_identifier),
                delta,
            )
            if flag == IntentFlag.RESOURCE_DELETION_PENDING_FLAG:
                intent: str = derive_deletion_intent_flag(
                    resource_name, resource_identifier
                )
            else:
                intent: str = create_creation_intent_flag(
                    flag,
                    resource_name,
                    action,
                    str(user_identifier),
                    str(resource_identifier),
                )
            self._pipeline_set_intent(pipeline, intent, self.cache_config.TTL_STRONGEST)

            self._pipeline_create_event(
                pipeline, StreamName.USER_INTERACTIONS, event, dict(event_body)
            )

            await pipeline.execute()
