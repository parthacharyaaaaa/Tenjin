from datetime import datetime
from hashlib import sha256
from uuid import uuid4


def generate_url_token() -> str:
    temp_url = uuid4().hex + datetime.now().strftime("%d%m%y%H%M%S")
    return sha256(temp_url.encode()).digest().decode("utf-8")
