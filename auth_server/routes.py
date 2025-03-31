from auth_server import auth, tokenManager
from auxillary.decorators import enforce_json, private
from auth_server.token_manager import TOKEN_STORE_INTEGRITY_ERROR
from flask import request, jsonify, Response, make_response, g
from werkzeug.exceptions import BadRequest, MethodNotAllowed, NotFound, Unauthorized, Forbidden, InternalServerError, HTTPException
import requests
import jwt.exceptions as JWT_exc
import time
import traceback
import secrets

@auth.after_request
def enforceMinCSP(response):
    if response:
        response.headers["Content-Security-Policy"] = auth.config["CSP"]

    return response
### Error Handlers ###
@auth.errorhandler(MethodNotAllowed)
def methodNotAllowed(e : MethodNotAllowed):
    response = {"message" : f"{getattr(e, 'description', 'Method Not Allowed')}"}
    if hasattr(e, "HTTP_type") and hasattr(e, "expected_HTTP_type"):
        response.update({"additional" : f"Expected {e.expected_HTTP_type}, got {e.HTTP_type}"})
    return jsonify(response), 405

@auth.errorhandler(NotFound)
def resource_not_found(e : NotFound):
    return jsonify({"message": "Requested resource could not be found."}), 404

@auth.errorhandler(Forbidden)
@auth.errorhandler(Unauthorized)
def forbidden(e : Forbidden | Unauthorized):
    response = jsonify({"message" : getattr(e, "description", "Resource Access Denied")})
    response.headers.update({"issuer" : "babel-auth-flow"})
    return response, 403

@auth.errorhandler(BadRequest)
@auth.errorhandler(KeyError)
def unexpected_request_format(e : BadRequest | KeyError):
    rBody = {"message" : getattr(e, "description", "Bad Request! Ensure proper request format")}
    if hasattr(e, "_additional_info"):
        rBody.update({"additional information" : e._additional_info})
    response = jsonify(rBody)
    return response, 400

@auth.errorhandler(JWT_exc.ExpiredSignatureError)
def exp_sign(e):
    return jsonify({"message" : "Your token has expired, please re-issue a new one"}), 401

@auth.errorhandler(TOKEN_STORE_INTEGRITY_ERROR)
def tk_integrity_err(e):
    return jsonify({"message" : "Token integrity check failed", "description" : getattr(e, "description", "Refresh token invalid")}), 403

@auth.errorhandler(Exception)
@auth.errorhandler(HTTPException)
@auth.errorhandler(InternalServerError)
def internalServerError(e : Exception):
    print(traceback.format_exc())
    print(e.__class__)
    return jsonify({"message" : getattr(e, "description", "An Error Occured"), "Additional Info" : getattr(e, "_additional_info", "There seems to be an issue with our service, please retry after some time or contact support")}), 500

### Endpoints ###

@auth.route("/login", methods = ["POST", "OPTIONS"])
# @CSRF_protect
@enforce_json
def login():
    if not ("identity" in g.REQUEST_JSON and "password" in g.REQUEST_JSON):
        raise BadRequest(f"POST /{request.root_path} expects identity and password in HTTP body")
    for key in auth.config["PRIVATE_COMM_KEYS"]:
        valid = requests.post(f"{auth.config['PROTOCOL']}://{auth.config['RESOURCE_SERVER_ORIGIN']}/users/login",
                            json = {"identity" : g.REQUEST_JSON["identity"], "password" : g.REQUEST_JSON["password"]},
                            headers={"PRIVATE-API-KEY" : key})
        
        if valid.status_code != 200:
            return jsonify({"message" : "Authentication Failed",
                            "response_message" : valid.json().get("message", "None")}), valid.status_code
        
        break
    
    response = valid.json()
    subject = response["sub"]
    aToken = tokenManager.issueAccessToken(sub = subject)
    rToken = tokenManager.issueRefreshToken(sub = subject,
                                            firstTime=True)

    epoch = time.time()
    response = jsonify({
        "message" : response.get("message", "Login complete."),
        "alias" : response.get('alias', None),
        "username" : response.get('sub'),
        "time_of_issuance" : epoch,
        "access_exp" : epoch + tokenManager.accessLifetime,
        "leeway" : tokenManager.leeway,
        "issuer" : "babel-auth-service",
    })
    response.set_cookie(key="access",
                        value=aToken,
                        max_age=tokenManager.accessLifetime + tokenManager.leeway,
                        httponly=True)
    response.set_cookie(key="refresh",
                        value=rToken,
                        max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                        httponly=True,
                        path="/reissue")
    response.set_cookie(key="refresh",
                    value=rToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/delete-account")
    response.set_cookie(key="refresh",
                    value=rToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/purge-family")
    return response, 201

@auth.route("/register", methods = ["POST", "OPTIONS"])
# @CSRF_protect
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
    for key in auth.config["PRIVATE_COMM_KEYS"]:
        valid = requests.post(f"{auth.config['PROTOCOL']}://{auth.config['RESOURCE_SERVER_ORIGIN']}/users/",
                            json = g.REQUEST_JSON,
                            headers={"PRIVATE-API-KEY" : key})
        
        if valid.status_code != 201:
            return jsonify({"message" : "Failed to create account",
                            "response_message" : valid.json().get("message", "Sowwy >:3")}), valid.status_code
        break
    
    response: dict = valid.json()
    subject = response["sub"]
    aToken = tokenManager.issueAccessToken(sub = subject)
    rToken = tokenManager.issueRefreshToken(sub = subject,
                                            firstTime=True)
    epoch = time.time()
    response = jsonify({
        "message" : response.get("message", "Registration complete."),
        "alias" : response.get('alias', None),
        "username" : response.get('sub'),
        "time_of_issuance" : epoch,
        "access_exp" : epoch + tokenManager.accessLifetime,
        "leeway" : tokenManager.leeway,
        "issuer" : "babel-auth-service",
    })

    response.set_cookie(key="access",
                        value=aToken,
                        max_age=tokenManager.accessLifetime + tokenManager.leeway,
                        httponly=True)
    response.set_cookie(key="refresh",
                        value=rToken,
                        max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                        httponly=True,
                        path="/reissue")
    response.set_cookie(key="refresh",
                        value=rToken,
                        max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                        httponly=True,
                        path="/delete-account")
    response.set_cookie(key="refresh",
                    value=rToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/purge-family")

    return response, 201

@auth.route("/delete-account", methods = ["DELETE"])
@private
def deleteAccount():
    tokenManager.invalidateFamily(request.headers["refreshID"])
    return jsonify({"message" : "resource deleted successfully"}), 204


@auth.route("/reissue", methods = ["GET", "OPTIONS"])
# @CSRF_protect
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
                        path="/reissue")
    response.set_cookie(key="refresh",
                    value=nRefreshToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/delete-account")
    response.set_cookie(key="refresh",
                    value=nRefreshToken,
                    max_age=tokenManager.refreshLifetime + tokenManager.leeway,
                    httponly=True,
                    path="/purge-family")
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
    response : Response = jsonify({"message" : "Token Revoked"})
    response.headers["iss"] = "babel-auth-service"
    return response, 204


@auth.route("/get-csrf", methods = ["OPTIONS", "GET"])
def issueCSRF():
    response = make_response()
    response.headers["X-CSRF-TOKEN"] = secrets.token_urlsafe(32)
    response.headers["TIME-OF-ISSUANCE"] = time.time()
    response.headers["ISSUER"] = "babel-auth-service"

    response.set_cookie(key="X-CSRF-TOKEN",
                        value=response.headers["X-CSRF-TOKEN"],
                        max_age=1200,
                        httponly=True)
    
    return response, 201

@auth.route("/ip-blacklist", methods = ["POST"])
def blacklist():
    ...

@auth.route("/get-blacklist", methods = ["GET"])
def getBlacklist():
    ...