import os
import multiprocessing
from dotenv import load_dotenv

if not load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), ".env"),
    verbose=True,
    override=True,
):
    print(
        f"Failed to load .env file for resource server, please ensure .env exists within this directory: {os.path.dirname(__file__)}"
    )
    raise FileNotFoundError

bind: str = f"0.0.0.0:{os.environ['FLASK_PORT']}"
workers: int = multiprocessing.cpu_count() * 2 + 1
threads: int = 4
worker_class: str = "gthread"

timeout: int = 30
graceful_timeout: int = 20
keepalive: int = 2
