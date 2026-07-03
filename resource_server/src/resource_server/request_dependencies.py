from datetime import datetime, timedelta
from typing import Annotated, Final

from fastapi import Depends, Query, Request
from fastapi.exceptions import HTTPException

import jwt
from jwt.exceptions import PyJWTError, ExpiredSignatureError

from auxillary.utils import from_base64url

from resource_server.config.app_config import AppConfig
from resource_server.dependencies import get_app_config, get_key_manager, get_genres
from resource_server.key_manager import KeyManager
from resource_server.models.database import Genre
from resource_server.utils.typing import StandardAccessTokenClaims
from resource_server.datastructures.requests import (
    SortOption,
    TIMEFRAMES,
    TimeFrameOption,
)


async def validate_access_token(
    request: Request,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    key_manager: Annotated[KeyManager, Depends(get_key_manager)],
) -> StandardAccessTokenClaims:
    """
    Protect an endpoint by validating an access token through cookies
    """
    if "Authorization" not in request.headers:
        raise HTTPException(401, "Authentication details missing")

    encoded_access_token: Final[str] = request.headers["Authorization"].split()[1]

    headers: dict[str, str] = jwt.get_unverified_header(encoded_access_token)
    key_id, alg = headers.get("kid"), headers.get("alg")

    # Early exit on visibly invaalid tokens
    if not key_id:
        raise HTTPException(401, "Invalid token, key ID missing")
    if alg not in app_config.JWKS.ALLOWED_ALGORITHMS:
        raise HTTPException(401, "Invalid token, unsupported algorithm claim")

    try:
        decoded_token: dict[str, str | int] | None = None
        if key := key_manager.current_mapping.get(key_id):
            decoded_token = jwt.decode(
                jwt=encoded_access_token,
                key=key,
                leeway=timedelta(minutes=app_config.JWKS.KEY_LEEWAY),
            )

            return StandardAccessTokenClaims(**decoded_token)  # type: ignore[reportArgumentType]

        else:
            # Update current mapping through global JWKS mapping
            key_manager.current_mapping = await key_manager.get_global_key_mapping()
            if key := key_manager.current_mapping.get(key_id):
                decoded_token = jwt.decode(
                    jwt=encoded_access_token,
                    key=key,
                    leeway=timedelta(minutes=app_config.JWKS.KEY_LEEWAY),
                )

                return StandardAccessTokenClaims(**decoded_token)  # type: ignore[reportArgumentType]

            raise HTTPException(
                401, "Invalid Key ID, no such key was found. Please login again"
            )

    except ExpiredSignatureError:
        raise HTTPException(401, "JWT token expired, begin refresh issuance")
    except PyJWTError as e:
        raise HTTPException(401, "JWT token invalid")


def cursor_preprocessor(
    raw_cursor: str | None = Query(default=None, alias="cursor")
) -> int:
    if not raw_cursor:
        return 0

    return from_base64url(raw_cursor)


def search_param_preprocessor(
    raw_search_param: str | None = Query(default=None, alias="search")
) -> str | None:
    if not raw_search_param:
        return None

    return raw_search_param.strip()


async def anime_genres_preprocessor(
    raw_genres: list[str] | None = Query(default=None, alias="genre")
) -> list[Genre] | None:
    if not raw_genres:
        return None

    raw_genres = [raw_genre.lower() for raw_genre in raw_genres]
    genres_dict: dict[str, Genre] = {g.name_.lower(): g for g in await get_genres()}

    residue: list[str] = []
    genres: list[Genre] = []
    for raw_genre in raw_genres:
        genre: Genre | None = genres_dict.get(raw_genre)
        if not genre:
            residue.append(raw_genre)
            continue
        genres.append(genre)

    if residue:
        raise ValueError(
            " ".join(
                (
                    f"No such genres found: {', '.join(residue)}",
                    f"Available genres: {', '.join(genres_dict.keys())}",
                )
            )
        )
    return genres


def preprocess_sort_option(
    raw_sort_option: str | None = Query(default=None, alias="cursor")
) -> SortOption:
    if not raw_sort_option:
        return SortOption.DESCENDING
    try:
        return SortOption(raw_sort_option.lower().strip())
    except ValueError:
        return SortOption.DESCENDING


def preprocess_timeframe(
    raw_timeframe_option: str | None = Query(default=None, alias="timeframe")
) -> tuple[TimeFrameOption, datetime]:
    if not raw_timeframe_option:
        return TimeFrameOption.ALL_TIME, TIMEFRAMES[TimeFrameOption.ALL_TIME](
            datetime.now()
        )
    try:
        timeframe_option = TimeFrameOption(raw_timeframe_option.strip().lower())
    except ValueError:
        timeframe_option = TimeFrameOption.ALL_TIME

    func = TIMEFRAMES.get(timeframe_option, TIMEFRAMES[TimeFrameOption.ALL_TIME])

    return timeframe_option, func(datetime.now())
