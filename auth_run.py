from auth_server.auth_app import create_app

if __name__ == '__main__':
    auth_app = create_app()
    auth_app.run(host=auth_app.config['HOST'], port=auth_app.config['PORT'], debug=True)