from functools import wraps
from flask import request, current_app, g
from werkzeug.exceptions import BadRequest, Unauthorized, InternalServerError
from datetime import timedelta
from jwt import decode
from jwt.exceptions import PyJWTError, ExpiredSignatureError

def enforce_json(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        if not request.mimetype or request.mimetype.split("/")[-1] != "json":
            raise BadRequest("Invalid mimetype in request headers (Content-Type), must be */json")
        g.REQUEST_JSON = request.get_json(force=True, silent=True)
        if not g.REQUEST_JSON:
            badReq = BadRequest("Request body non-json parsable")
            badReq.__setattr__("details", "Please ensure that request body is formatted as JSON and does not contain any syntax errors or invalid data types")
            raise badReq
        return endpoint(*args, **kwargs)
    return decorated

def require_intraservice_key(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        key : str = request.headers.get("X-INTERSERVICE-KEY")
        if not key:
            raise Unauthorized("Access Denied: missing interservice key")
        if key not in current_app.config["VALID_API_KEYS"]:
            raise Unauthorized("Access Denied: invalid intraservice key")
        
        return endpoint(*args, **kwargs)
    return decorated

def token_required(endpoint):
    '''
    Protect an endpoint by validating an access token. Requires the header "Authorization: Bearer <credentials>". 
    Furthermore, sets global data (flask.g : _AppCtxGlobals) for usage of token details in the decorated endpoint
    '''
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        try:
            auth_metadata = request.cookies.get("access", request.cookies.get("Access"))
            if not auth_metadata:
                raise Unauthorized("Authentication details missing")
            decodedToken = decode(
                                jwt=auth_metadata.split()[-1],
                                key=current_app.config["SIGNING_KEY"],
                                algorithms=["HS256"],
                                issuer="babel-auth-service",
                                leeway=timedelta(minutes=3)
            )
            g.decodedToken = decodedToken
        except KeyError as e:
            raise BadRequest(f"Endpoint /{request.path[1:]} requires an authorization token to give access to resource")
        except ExpiredSignatureError:
            raise Unauthorized("JWT token expired, begin refresh issuance")
        except PyJWTError as e:
            raise Unauthorized("JWT token invalid")
        
        return endpoint(*args, **kwargs)
    return decorated

def private(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        try:
            # IP check
            request_ip = request.headers.get("X-FORWARDED-FOR", request.remote_addr)
            if not (request_ip in current_app.config["PRIVATE_IP_ADDRS"]):
                raise Unauthorized()
            
            # HTTP check
            if not request.headers["PRIVATE-API-KEY"] in current_app.config["PRIVATE_COMM_KEYS"]:
               raise Unauthorized("Access Denied >:(")
            
        except KeyError:
               if not request.headers.get("PRIVATE-API-KEY"):
                   raise Unauthorized("Private endpoint, requires an API key")
               else:
                   raise InternalServerError()
        return endpoint(*args, **kwargs)
    return decorated
