from auth_server import auth

auth.run(host=auth.config['HOST'], port=auth.config['PORT'], debug=True)