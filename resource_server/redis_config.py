import os
from dotenv import load_dotenv

bLoaded : bool = load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"),
                             verbose=True, override=True)
if not bLoaded:
    print(f"Failed to load .env file for resource server, please ensure .env exists within this directory: {os.path.dirname(__file__)}")
    raise FileNotFoundError

class RedisConfig:
    TTL_CAP: int = int(os.environ["TTL_CAP"])
    TTL_PROMOTION: int = int(os.environ["TTL_PROMOTION"])
    TTL_STRONGEST: int = int(os.environ['TTL_STRONGEST'])
    TTL_STRONG: int = int(os.environ["TTL_STRONG"])
    TTL_WEAK: int = int(os.environ["TTL_WEAK"])
    TTL_EPHEMERAL: int = int(os.environ["TTL_EPHEMERAL"])
    ANNOUNCEMENT_DURATION: int = int(os.environ.get('ANNOUNCEMENT_DURATION', 300))
    JWKS_POLL_COOLDOWN: int = int(os.environ.get('JWKS_POLL_COOLDOWN', 600))

    RESOURCE_CREATION_FLAG: str = '1'
    RESOURCE_DELETION_FLAG: str = '0'
    RESOURCE_CREATION_ALT_FLAG: str = '-1'    # Special value for post_votes

    NF_SENTINAL: str = '-1'