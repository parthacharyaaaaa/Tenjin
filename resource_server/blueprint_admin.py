from flask import Blueprint
admin = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/admins")