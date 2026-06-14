"""Package for holding all blueprints"""

from typing import Final

from fastapi import APIRouter

from resource_server.blueprints.url_prefixes import URLPrefix
from resource_server.blueprints.users import USERS
from resource_server.blueprints.animes import ANIMES
from resource_server.blueprints.forums import FORUMS
from resource_server.blueprints.posts import POSTS
from resource_server.blueprints.comments import COMMENTS
from resource_server.blueprints.misc import MISC

type t_route_prefixes = tuple[tuple[APIRouter, tuple[URLPrefix, ...]], ...]

ROUTER_PREFIXES: Final[t_route_prefixes] = tuple(
    (
        (USERS, (URLPrefix.USERS,)),
        (ANIMES, (URLPrefix.ANIMES,)),
        (POSTS, (URLPrefix.POSTS,)),
        (COMMENTS, (URLPrefix.POSTS, URLPrefix.COMMENTS)),
        (MISC, ()),
    )
)
