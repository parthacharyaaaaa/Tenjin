from functools import wraps
from flask import request, current_app, g
from werkzeug.exceptions import BadRequest, Unauthorized, InternalServerError

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
