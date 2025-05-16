from flask import Flask
from flask_cors import CORS
from auth_server.config import flaskconfig
from auth_server.token_manager import TokenManager

auth = Flask(__name__)
auth.config.from_object(flaskconfig)

cors = CORS(auth,
     resources={r"/*": {"origins": [f"http://127.0.{i}.{j}:6090" for i in range(0, 256) for j in range(0, 256)]}},
     supports_credentials=True)

# Set up token manager
tokenManager = TokenManager(signingKey=auth.config["SIGNING_KEY"], 
                            host = auth.config["REDIS_HOST"], 
                            port = auth.config["REDIS_PORT"], 
                            db = auth.config["REDIS_DB"])

from auth_server import routes