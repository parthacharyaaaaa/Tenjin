from typing import Annotated, Final
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from redis.asyncio import Redis

from resource_auxillary.strings import StreamName

from resource_server.dependencies import get_genres, get_app_redis_client
from resource_server.models.database import Genre, UserTicket
from resource_server.models.requests import UserTicketModel

MISC: Final[APIRouter] = APIRouter()


@MISC.get("/genres")
async def get_anime_genres() -> JSONResponse:
    genres: list[Genre] = await get_genres()

    return JSONResponse({g.name_: g.id_ for g in genres})


@MISC.post("/tickets")
async def issue_ticket(
    request_model: UserTicketModel,
    redis_client: Annotated[Redis, Depends(get_app_redis_client)],
) -> JSONResponse:
    time_raised_iso: str = datetime.now().isoformat()
    await redis_client.xadd(
        StreamName.INSERTIONS.value,
        fields={
            "email": request_model.email,
            "time_raised": time_raised_iso,
            "description": request_model.description,
            "table": UserTicket.__tablename__,
        },
    )

    return JSONResponse(
        {
            "message": "Your report has been recorded",
            "email": request_model.email,
            "time": time_raised_iso,
        },
        202,
    )
