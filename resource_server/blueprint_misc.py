from flask import Blueprint, jsonify, Response, current_app
misc = Blueprint('Misc', 'misc', url_prefix="/")

from resource_server.models import db, Genre
from sqlalchemy import select

@misc.route('/genres', methods=['GET'])
def get_genres() -> tuple[Response, int]:
    return jsonify(dict(current_app.config['GENRES'])), 200