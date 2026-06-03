from datetime import timedelta
from typing import Annotated, Final

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from resource_server.config.app_config import AppConfig
from resource_server.dependencies import get_app_config, get_key_manager
from resource_server.key_manager import KeyManager
from resource_server.utils.typing import StandardAccessTokenClaims

import jwt
from jwt.exceptions import PyJWTError, ExpiredSignatureError


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
