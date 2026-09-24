import hashlib
from datetime import UTC, datetime
from typing import Annotated, Final

from auxillary.data_structures.enriched.hypermedia import HypermediaResponseSequence
from auxillary.data_structures.enriched.link_builder import HypermediaLinkBuilder
from auxillary.data_structures.enriched.response import EnrichedJSONResponse
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from resource_auxillary.datastructures.payloads.standalone import UserTicket
from resource_auxillary.events import Event, EventSideEffects
from resource_auxillary.strings import Action, EventName, StreamName

from resource_server.cache_manager import CacheManager
from resource_server.dependencies import (
    get_app_redis_client,
    get_cache_manager,
    get_event_streamer,
    get_genres,
    get_hypermedia_link_builder,
)
from resource_server.event_streamer import EventStreamer
from resource_server.models.database import Genre
from resource_server.models.requests import UserTicketModel

MISC: Final[APIRouter] = APIRouter()


@MISC.get("/genres")
async def get_anime_genres(
    link_builder: Annotated[
        HypermediaLinkBuilder, Depends(get_hypermedia_link_builder)
    ],
) -> EnrichedJSONResponse:
    genres: list[Genre] = await get_genres()

    return EnrichedJSONResponse(
        {g.name_: g.id_ for g in genres},
        hypermedia_data=HypermediaResponseSequence(
            links=[
                link_builder.link_self(),
                link_builder.link("get_animes", "animes"),
            ]
        ),
        hypermedia_links_key="_links",
    )


@MISC.post("/tickets")
async def issue_ticket(
    request_model: UserTicketModel,
    redis_client: Annotated[Redis, Depends(get_app_redis_client)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
) -> JSONResponse:
    time_raised: datetime = datetime.now(UTC)

    description_identifier: Final[str] = hashlib.sha1(
        request_model.description.encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    async with cache_manager.guard_action(
        request_model.email,
        description_identifier,
        description_identifier,
        Action.CREATE,
    ):
        event: Event = Event(
            name=EventName.USER_TICKET,
            payload=UserTicket(
                email=request_model.email,
                time_raised=time_raised,
                description=request_model.description,
            ),
            side_effects=EventSideEffects(),
        )
        await event_streamer.emit_user_event(StreamName.USERS, event)
        return JSONResponse(
            {
                "message": "Your report has been recorded",
                "email": request_model.email,
                "time": time_raised,
            },
            202,
        )
