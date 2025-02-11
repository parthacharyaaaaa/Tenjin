from flask import Blueprint
user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")