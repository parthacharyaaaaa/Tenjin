import time
from hashlib import sha256
from typing import Annotated, Final

import aiofiles

from fastapi import APIRouter, Depends
from fastapi.requests import Request
from fastapi.responses import Response, JSONResponse

import httpx

from auxillary.decorators import enforce_json

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import get_app_config, get_token_manager
from auth_server.models.auth_requests import AuthenticationModel, RegistrationModel
from auth_server.security.token_manager import TokenManager
from auth_server.security.tokens import TokenType, StandardRefreshTokenClaims
from auth_server.utils.auth_auxillary import attach_tokens

AUTH: Final[APIRouter] = APIRouter()

config: Final[AppConfig] = get_app_config()


### Endpoints ###
@AUTH.get("/jwks.json")
async def jwks() -> Response:
    # TODO: Add Cache-Control, and using an in-memory jwks copy
    async with aiofiles.open(config.JWKS.JWKS_FILEPATH, mode="rb") as jwks_file:
        return Response(await jwks_file.read(), media_type="application/json")


@AUTH.post("/login")
@enforce_json
async def login(
    request: Request,
    auth_model: AuthenticationModel,
    token_manager: Annotated[TokenManager, Depends(get_token_manager)],
):
    # TODO: Add proper handling of 'request_for' field

    async with httpx.AsyncClient() as http_client:
        resource_response: httpx.Response = await http_client.post(
            url="", json=auth_model.model_dump_json()
        )

    if resource_response.status_code != 200:
        return JSONResponse(
            {
                "message": "Authentication Failed",
                "response_message": resource_response.json().get("message", "None"),
            },
            resource_response.status_code,
        )

    response_contents: dict[str, str | int] = resource_response.json()
    sub, sid = str(response_contents.pop("sub")), int(response_contents.pop("sid"))
    family_id: str = sha256(f"{sub}:{sid}".encode()).hexdigest()
    access_token: str = token_manager.issueAccessToken(sub, sid, family_id)
    refresh_token: str = token_manager.issueRefreshToken(sub, sid, familyID=family_id)

    epoch: float = time.time()
    response: JSONResponse = JSONResponse(
        {
            "message": response_contents.pop("message", "Login complete."),
            "username": sub,
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "tenjin-auth-service",
            "_additional": {**response_contents},
        }
    )

    attach_tokens(
        response,
        access_token,
        refresh_token,
        token_manager.accessLifetime + token_manager.leeway,
        token_manager.refreshLifetime + token_manager.leeway,
        paths=[request.url_for("reissue"), request.url_for("purge_family")],
    )
    return response, 201


@AUTH.post("/register")
@enforce_json
async def register(
    request: Request,
    registration_model: RegistrationModel,
    token_manager: Annotated[TokenManager, Depends(get_token_manager)],
):
    async with httpx.AsyncClient() as http_client:
        resource_response = await http_client.post(
            "",
            json=registration_model.model_dump_json(),
        )

    if resource_response.status_code != 201:
        return JSONResponse(
            {
                "message": "Failed to create account",
                "response_message": resource_response.json().get("message"),
            },
            resource_response.status_code,
        )

    response_contents: dict[str, str | int] = resource_response.json()
    sub, sid = str(response_contents.pop("sub")), int(response_contents.pop("sid"))
    family_id: str = sha256(f"{sub}:{sid}".encode()).hexdigest()
    access_token: str = token_manager.issueAccessToken(sub, sid, family_id)
    refresh_token: str = token_manager.issueRefreshToken(sub, sid, familyID=family_id)
    epoch: float = time.time()
    response: JSONResponse = JSONResponse(
        {
            "message": response_contents.pop("message", "Registration complete."),
            "username": sub,
            "email": response_contents.pop("email", None),
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "tenjin-AUTH-service",
            "_additional": {**response_contents},
        },
        status_code=201,
    )

    attach_tokens(
        response,
        access_token,
        refresh_token,
        token_manager.accessLifetime + token_manager.leeway,
        token_manager.refreshLifetime + token_manager.leeway,
        paths=[request.url_for("reissue"), request.url_for("purge_family")],
    )
    return response, 201


@AUTH.get("/reissue")
def reissue(
    request: Request, token_manager: Annotated[TokenManager, Depends(get_token_manager)]
):
    refresh_token: str | None = request.cookies.get(
        "refresh", request.cookies.get("Refresh")
    )

    if not refresh_token:
        e = KeyError()
        setattr(
            e, "description", "Refresh Token missing from request, reissuance denied"
        )
        raise e

    new_refresh_token, new_access_token = token_manager.reissueTokenPair(refresh_token)
    epoch: float = time.time()
    response: Response = JSONResponse(
        {
            "message": "Reissuance successful",
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "babel-AUTH-service",
        }
    )

    attach_tokens(
        response,
        new_access_token,
        new_refresh_token,
        token_manager.accessLifetime + token_manager.leeway,
        token_manager.refreshLifetime + token_manager.leeway,
        paths=[request.url_for("reissue"), request.url_for("purge_family")],
    )
    return response, 201


@AUTH.delete("/tokens")
def purge_family(
    request: Request, token_manager: Annotated[TokenManager, Depends(get_token_manager)]
):
    """
    Purges an entire token family in case of a reuse attack or a normal client logout
    """
    encoded_refresh_token: str | None = request.cookies.get(
        "Refresh", request.cookies.get("refresh")
    )
    if not encoded_refresh_token:
        raise BadRequest(f"Logout requires a refresh token to be provided")

    try:
        refresh_token: StandardRefreshTokenClaims = token_manager.decodeToken(
            encoded_refresh_token,
            TokenType.StandardRefresh,
            options={"verify_nbf": False},
        )
        token_manager.invalidateFamily(refresh_token["fid"])
    except:
        raise Unauthorized("Failed to validate this refresh token")

    return JSONResponse({"message": "Token Revoked"})
