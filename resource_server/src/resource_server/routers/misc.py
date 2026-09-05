from resource_auxillary.strings import StreamName
from resource_auxillary.events import EventSideEffects
from resource_auxillary.strings import EventName
from resource_auxillary.events import Event
from resource_auxillary.strings import Action
from resource_server.dependencies import get_cache_manager
from resource_server.cache_manager import CacheManager
from resource_server.dependencies import get_event_streamer
from resource_server.event_streamer import EventStreamer
from typing import Annotated, Final
from datetime import datetime
import hashlib

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from redis.asyncio import Redis

from resource_server.dependencies import get_genres, get_app_redis_client
from resource_server.models.database import Genre
from resource_server.models.requests import UserTicketModel

from resource_auxillary.datastructures.payloads.standalone import UserTicket

MISC: Final[APIRouter] = APIRouter()


@MISC.get("/genres")
async def get_anime_genres() -> JSONResponse:
    genres: list[Genre] = await get_genres()

    return JSONResponse({g.name_: g.id_ for g in genres})


@MISC.post("/tickets")
async def issue_ticket(
    request_model: UserTicketModel,
    redis_client: Annotated[Redis, Depends(get_app_redis_client)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
) -> JSONResponse:
    time_raised: datetime = datetime.now()

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
