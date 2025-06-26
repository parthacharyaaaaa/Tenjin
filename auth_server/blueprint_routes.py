from auth_server.token_manager import tokenManager
from auxillary.decorators import enforce_json
from flask import Blueprint, request, jsonify, Response, g, current_app, send_from_directory, url_for
from werkzeug.exceptions import BadRequest
import requests
import time
from typing import Any
from hashlib import sha256

auth: Blueprint = Blueprint('auth', 'auth', url_prefix='/auth')

@auth.after_request
def enforceMinCSP(response):
    if response:
        response.headers["Content-Security-Policy"] = current_app.config["CSP"]

    return response

### Endpoints ###
@auth.route('/jwks.json')
def jwks() -> tuple[Response, int]:
    return send_from_directory(current_app.instance_path, current_app.config['JWKS_FILENAME'], mimetype='application/json')
    

@auth.route("/login", methods = ["POST", "OPTIONS"])
@enforce_json
def login():
    if not ("identity" in g.REQUEST_JSON and "password" in g.REQUEST_JSON):
        raise BadRequest(f"POST /{request.root_path} expects identity and password in HTTP body")
    valid = requests.post(f"{current_app.config['PROTOCOL']}://{current_app.config['RESOURCE_SERVER_ORIGIN']}{current_app.config['RESOURCE_SERVER_URL_PREFIX']}/users/login",
                        json = {"identity" : g.REQUEST_JSON["identity"], "password" : g.REQUEST_JSON["password"]})
    
    if valid.status_code != 200:
        return jsonify({"message" : "Authentication Failed",
                        "response_message" : valid.json().get("message", "None")}), valid.status_code
        
    
    rsResponse: dict = valid.json()
    sub, sid = rsResponse.pop('sub'), rsResponse.pop('sid')
    familyID: str = sha256(f'{sub}:{sid}'.encode()).hexdigest()
    aToken = tokenManager.issueAccessToken(sub, sid, familyID)
    rToken = tokenManager.issueRefreshToken(sub, sid, familyID=familyID, reissuance=False)

    epoch = time.time()
    response = jsonify({
        "message" : rsResponse.pop("message", "Login complete."),
        "username" : sub,
        "time_of_issuance" : epoch,
        "access_exp" : epoch + tokenManager.accessLifetime,
        "leeway" : tokenManager.leeway,
        "issuer" : "babel-auth-service"
    })
    tokenManager.attach_tokens_to_response(response, access_token=aToken, refresh_token=rToken, paths=[url_for('.reissue')])
    return response, 201

@auth.route("/register", methods = ["POST", "OPTIONS"])
@enforce_json
def register():    
    if not ("username" in g.REQUEST_JSON and
            "email" in g.REQUEST_JSON and
            "password" in g.REQUEST_JSON and
            "cpassword" in g.REQUEST_JSON):
        raise BadRequest("Mandatory field missing")
    
    if g.REQUEST_JSON["password"] != g.REQUEST_JSON["cpassword"]:
        raise BadRequest("Passwords do not match")
    
    g.REQUEST_JSON.update({"authprovider" : "babel-auth"})
    valid = requests.post(f"{current_app.config['PROTOCOL']}://{current_app.config['RESOURCE_SERVER_ORIGIN']}{current_app.config['RESOURCE_SERVER_URL_PREFIX']}/users/",
                        json = g.REQUEST_JSON)
    
    if valid.status_code != 201:
        return jsonify({"message" : "Failed to create account",
                        "response_message" : valid.json().get("message", "Sowwy >:3")}), valid.status_code
    
    rsResponse: dict[str, Any] = valid.json()
    sub, sid = rsResponse.pop('sub'), rsResponse.pop('sid')
    familyID: str = sha256(f'{sub}:{sid}'.encode()).hexdigest()
    aToken = tokenManager.issueAccessToken(sub, sid, familyID)
    rToken = tokenManager.issueRefreshToken(sub, sid, familyID=familyID, reissuance=False)
    epoch = time.time()
    response = jsonify({"message" : rsResponse.pop("message", "Registration complete."),
        "username" : sub,
        "email" : rsResponse.pop('email', None),
        "time_of_issuance" : epoch,
        "access_exp" : epoch + tokenManager.accessLifetime,
        "leeway" : tokenManager.leeway,
        "issuer" : "babel-auth-service",
        "_additional" : {**rsResponse}
    })

    tokenManager.attach_tokens_to_response(response, access_token=aToken, refresh_token=rToken, paths=[url_for('.reissue')])
    return response, 201

@auth.route("/reissue", methods = ["GET", "OPTIONS"])
def reissue():
    refreshToken = request.cookies.get("refresh", request.cookies.get("Refresh"))

    if not refreshToken:
        e = KeyError()
        e.__setattr__("description", "Refresh Token missing from request, reissuance denied")
        raise e
    
    nRefreshToken, nAccessToken = tokenManager.reissueTokenPair(refreshToken)
    epoch = time.time()
    response = jsonify({
        "message" : "Reissuance successful",
        "time_of_issuance" : epoch,
        "access_exp" : epoch + tokenManager.accessLifetime,
        "leeway" : tokenManager.leeway,
        "issuer" : "babel-auth-service"
    })

    response.set_cookie(key="access",
                        value=nAccessToken,
                        max_age=tokenManager.accessLifetime + tokenManager.leeway,
                        httponly=True)
    response.set_cookie(key="refresh",
                        value=nRefreshToken,
                        max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                        httponly=True,
                        path="/auth/reissue")
    response.set_cookie(key="refresh",
                    value=nRefreshToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/auth/delete-account")
    response.set_cookie(key="refresh",
                    value=nRefreshToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/auth/purge-family")
    return response, 201

@auth.route("/purge-family", methods = ["GET", "OPTIONS"])
def purgeFamily():
    '''
    Purges an entire token family in case of a reuse attack or a normal client logout
    '''
    tkn = request.cookies.get("Refresh", request.cookies.get("refresh"))
    if not tkn:
        raise BadRequest(f"Logout requires a refresh token to be provided")
    
    tkn = tokenManager.decodeToken(tkn,
                                   tType="refresh",
                                   options={"verify_nbf" : False})
    if not tkn:
        raise BadRequest(f"Invalid Refresh Token provided to [{request.method}] {request.url_rule}")
    
    tokenManager.invalidateFamily(tkn['fid'])
    return jsonify({"message" : "Token Revoked"}), 200
