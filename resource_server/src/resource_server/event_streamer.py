from dataclasses import dataclass

from redis.asyncio.client import Redis, Pipeline

from auxillary.utils import cache_repr

from resource_server.config.sub_config import CacheConfig
from auxillary.singleton import SingletonMetaclass

from resource_auxillary.events import Event
from resource_auxillary.strings import StreamName


@dataclass(slots=True, weakref_slot=True)
class EventStreamer(metaclass=SingletonMetaclass):

    redis_client: Redis
    cache_config: CacheConfig

    def _pipeline_update_counter(
        self, pipeline: Pipeline, hashmap_name: str, identifier: str, delta: int
    ) -> None:
        """
        Update the global counter for a resource's field
        """
        pipeline.hincrby(hashmap_name, identifier, delta)

    def _pipeline_set_intent(
        self, pipeline: Pipeline, name: str, intent: str, ttl: int
    ) -> None:
        pipeline.set(name, intent, ex=ttl)

    def _pipeline_create_event(
        self,
        pipeline: Pipeline,
        stream: StreamName,
        event: Event,
    ) -> None:
        pipeline.xadd(stream.value, cache_repr(event), nomkstream=False)  # type: ignore

    async def emit_user_event(self, stream: StreamName, event: Event) -> None:
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            # Perform event-side effects, apart from cache invalidation
            for counter_update in event.side_effects.counter_updates:
                self._pipeline_update_counter(
                    pipeline,
                    counter_update.counter_group,
                    counter_update.cache_key,
                    counter_update.delta,
                )
            for intent_update in event.side_effects.intent_updates:
                self._pipeline_set_intent(
                    pipeline,
                    intent_update.intent_name,
                    intent_update.intent_value,
                    self.cache_config.TTL_STRONGEST,
                )
            self._pipeline_create_event(pipeline, stream, event)

            await pipeline.execute()
