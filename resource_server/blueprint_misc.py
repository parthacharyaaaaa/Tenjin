from flask import Blueprint, jsonify, current_app, g
from werkzeug import Response
from werkzeug.exceptions import BadRequest
from resource_server.models import UserTicket
from auxillary.decorators import enforce_json
from resource_server.resource_decorators import pass_user_details
from resource_server.resource_auxillary import EMAIL_REGEX
from resource_server.external_extensions import RedisInterface
import re
from datetime import datetime

MISC_BLUEPRINT: Blueprint = Blueprint('Misc', 'misc')

@MISC_BLUEPRINT.route('/genres', methods=['GET'])
def get_genres() -> tuple[Response, int]:
    return jsonify(dict(current_app.config['GENRES'])), 200

@MISC_BLUEPRINT.route('/tickets', methods=['POST'])
@enforce_json
@pass_user_details
def issue_ticket() -> tuple[Response, int]:
    email: str = g.REQUEST_JSON.get('email', '').strip()
    if not re.match(EMAIL_REGEX, email):
        raise BadRequest("Valid email address is required to report an issue")
    
    description: str = g.REQUEST_JSON.get('description', '').strip()
    if len(description) < 8:
        raise BadRequest('Description of the issue must be atleast 8 characters long')
    
    time_raised_iso: str = datetime.now().isoformat()
    RedisInterface.xadd('INSERTIONS', fields={'user_id' : '' if not g.REQUESTING_USER else g.REQUESTING_USER.get('sid', ''),
                                              'email' : email,
                                              'time_raised': time_raised_iso,
                                              'description' : description,
                                              'table' : UserTicket.__tablename__})
    return jsonify({'message' :'Your report has been recorded', 'email' : email, 'description' : description, 'time' : time_raised_iso}), 202