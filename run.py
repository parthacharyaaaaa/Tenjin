from resource_server.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host=app.config['APP_HOST'], port=app.config['APP_PORT'], debug=True)