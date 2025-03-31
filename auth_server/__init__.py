from flask import Flask
from auth_server.config import flaskconfig
from auth_server.token_manager import TokenManager

auth = Flask(__name__)
auth.config.from_object(flaskconfig)

# Set up token manager
tokenManager = TokenManager(signingKey=auth.config["SIGNING_KEY"], 
                            host = auth.config["REDIS_HOST"], 
                            port = auth.config["REDIS_PORT"], 
                            db = auth.config["REDIS_DB"])

from auth_server import routes