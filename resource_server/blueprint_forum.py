from flask import Blueprint
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/animes")

@forum.route("/", methods=["GET", "HEAD"])
def index():
    return "<!DOCTYPE HTML> <html> Hello :3 </html>"