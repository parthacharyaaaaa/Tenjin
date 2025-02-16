import orjson
from functools import wraps
from typing import Literal

from flask import request, current_app, g
from werkzeug.exceptions import BadRequest, Unauthorized

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