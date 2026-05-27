from typing import Final

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import get_app_config, get_token_manager
from auth_server.token_manager import TokenManager
from auth_server.tokens import TokenType, StandardRefreshTokenClaims
from auxillary.decorators import enforce_json
from flask import (
    Blueprint,
    request,
    jsonify,
    Response,
    g,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import BadRequest, Unauthorized, UnprocessableEntity
import requests
import time
from hashlib import sha256

auth: Blueprint = Blueprint("auth", "auth")

config: Final[AppConfig] = get_app_config()


### Endpoints ###
@auth.route("/jwks.json")
def jwks() -> tuple[Response, int]:
    return (
        send_from_directory(
            config.JWKS.JWKS_FILEPATH.parent,
            config.JWKS.JWKS_FILEPATH.name,
            mimetype="application/json",
        ),
        200,
    )


@auth.route("/login", methods=["POST", "OPTIONS"])
@enforce_json
def login():
    token_manager: Final[TokenManager] = get_token_manager()
    # TODO: Add proper handling of 'request_for' field

    if not ("request_for") in g.REQUEST_JSON:
        raise UnprocessableEntity("Missing 'request_for' field")
    if not ("identity" in g.REQUEST_JSON and "password" in g.REQUEST_JSON):
        raise BadRequest(
            f"POST /{request.root_path} expects identity and password in HTTP body"
        )
    valid = requests.post(
        g.REQUEST_JSON["request_for"],
        json={
            "identity": g.REQUEST_JSON["identity"],
            "password": g.REQUEST_JSON["password"],
        },
    )

    if valid.status_code != 200:
        return (
            jsonify(
                {
                    "message": "Authentication Failed",
                    "response_message": valid.json().get("message", "None"),
                }
            ),
            valid.status_code,
        )

    rsResponse: dict[str, str | int] = valid.json()
    sub, sid = str(rsResponse.pop("sub")), int(rsResponse.pop("sid"))
    familyID: str = sha256(f"{sub}:{sid}".encode()).hexdigest()
    aToken: str = token_manager.issueAccessToken(sub, sid, familyID)
    rToken: str = token_manager.issueRefreshToken(sub, sid, familyID=familyID)

    epoch: float = time.time()
    response: Response = jsonify(
        {
            "message": rsResponse.pop("message", "Login complete."),
            "username": sub,
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "tenjin-auth-service",
            "_additional": {**rsResponse},
        }
    )
    token_manager.attach_tokens_to_response(
        response,
        access_token=aToken,
        refresh_token=rToken,
        paths=[url_for(".reissue"), url_for(".purgeFamily")],
    )
    return response, 201


@auth.route("/register", methods=["POST", "OPTIONS"])
@enforce_json
def register():
    token_manager: Final[TokenManager] = get_token_manager()
    if not (
        "username" in g.REQUEST_JSON
        and "email" in g.REQUEST_JSON
        and "password" in g.REQUEST_JSON
        and "request_for" in g.REQUEST_JSON
    ):
        raise BadRequest("Mandatory field missing")

    g.REQUEST_JSON.update({"authprovider": "tenjin-auth"})
    valid = requests.post(
        g.REQUEST_JSON["request_for"],
        json=g.REQUEST_JSON,
    )

    if valid.status_code != 201:
        return (
            jsonify(
                {
                    "message": "Failed to create account",
                    "response_message": valid.json().get("message", "Sowwy >:3"),
                }
            ),
            valid.status_code,
        )

    rsResponse: dict[str, str | int] = valid.json()
    sub, sid = str(rsResponse.pop("sub")), int(rsResponse.pop("sid"))
    familyID: str = sha256(f"{sub}:{sid}".encode()).hexdigest()
    aToken: str = token_manager.issueAccessToken(sub, sid, familyID)
    rToken: str = token_manager.issueRefreshToken(sub, sid, familyID=familyID)
    epoch: float = time.time()
    response: Response = jsonify(
        {
            "message": rsResponse.pop("message", "Registration complete."),
            "username": sub,
            "email": rsResponse.pop("email", None),
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "tenjin-auth-service",
            "_additional": {**rsResponse},
        }
    )

    token_manager.attach_tokens_to_response(
        response,
        access_token=aToken,
        refresh_token=rToken,
        paths=[url_for(".reissue"), url_for(".purgeFamily")],
    )
    return response, 201


@auth.route("/reissue", methods=["GET", "OPTIONS"])
def reissue():
    token_manager: Final[TokenManager] = get_token_manager()
    refreshToken: str | None = request.cookies.get(
        "refresh", request.cookies.get("Refresh")
    )

    if not refreshToken:
        e = KeyError()
        e.__setattr__(
            "description", "Refresh Token missing from request, reissuance denied"
        )
        raise e

    nRefreshToken, nAccessToken = token_manager.reissueTokenPair(refreshToken)
    epoch: float = time.time()
    response: Response = jsonify(
        {
            "message": "Reissuance successful",
            "time_of_issuance": epoch,
            "access_exp": epoch + token_manager.accessLifetime,
            "leeway": token_manager.leeway,
            "issuer": "babel-auth-service",
        }
    )

    token_manager.attach_tokens_to_response(
        response,
        access_token=nAccessToken,
        refresh_token=nRefreshToken,
        paths=[url_for(".reissue"), url_for(".purgeFamily")],
    )
    return response, 201


@auth.route("/tokens", methods=["DELETE", "OPTIONS"])
def purgeFamily():
    """
    Purges an entire token family in case of a reuse attack or a normal client logout
    """
    token_manager: Final[TokenManager] = get_token_manager()
    encodedRefreshToken: str | None = request.cookies.get(
        "Refresh", request.cookies.get("refresh")
    )
    if not encodedRefreshToken:
        raise BadRequest(f"Logout requires a refresh token to be provided")

    try:
        refreshToken: StandardRefreshTokenClaims = token_manager.decodeToken(
            encodedRefreshToken,
            TokenType.StandardRefresh,
            options={"verify_nbf": False},
        )
        token_manager.invalidateFamily(refreshToken["fid"])
    except:
        raise Unauthorized("Failed to validate this refresh token")

    return jsonify({"message": "Token Revoked"}), 200
