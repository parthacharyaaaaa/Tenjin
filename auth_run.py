from auth_server.auth_app import create_app
from auth_server.models import *

if __name__ == '__main__':
    auth_app = create_app()
    with auth_app.app_context():
        db.create_all()
    auth_app.run(host=auth_app.config['HOST'], port=auth_app.config['PORT'], debug=True)