from flask import Blueprint
config = Blueprint("config", "config", url_prefix="cmd")


from auxillary.decorators import require_intraservice_key
@config.before_request
def enforce_whitelist():
    require_intraservice_key(lambda : None)()