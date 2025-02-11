import orjson
from functools import wraps
from typing import Literal

from flask import request, current_app, g
from werkzeug.exceptions import BadRequest

with open("config.json", "r") as configFile:
    CONFIG = orjson.loads(configFile)


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