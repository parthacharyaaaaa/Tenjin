from flask import Blueprint, jsonify, current_app, g
from werkzeug import Response
from werkzeug.exceptions import BadRequest
misc = Blueprint('Misc', 'misc', url_prefix="/")

from resource_server.models import UserTicket
from auxillary.decorators import enforce_json
from resource_server.resource_decorators import pass_user_details
from resource_server.resource_auxillary import EMAIL_REGEX
from resource_server.external_extensions import RedisInterface
import re
from datetime import datetime

@misc.route('/genres', methods=['GET'])
def get_genres() -> tuple[Response, int]:
    return jsonify(dict(current_app.config['GENRES'])), 200

@misc.route('/ticket', methods=['POST'])
@enforce_json
@pass_user_details
def issue_ticket() -> tuple[Response, int]:
    email: str = g.REQUEST_JSON.get('email', '').strip()
    if not re.match(EMAIL_REGEX, email):
        raise BadRequest("Valid email address is required to report an issue")
    
    description: str = g.REQUEST_JSON.get('description', '').strip()
    if len(description) < 8:
        raise BadRequest('Description of the issue must be atleast 8 characters long')
    
    RedisInterface.xadd('INSERTIONS', fields={'user_id' : '' if not g.REQUESTING_USER else g.REQUESTING_USER.get('sid', ''),
                                              'email' : email,
                                              'time_raised': datetime.now().isoformat(),
                                              'description' : description,
                                              'table' : UserTicket.__tablename__})
    return jsonify({'Your report has been recorded'}), 202