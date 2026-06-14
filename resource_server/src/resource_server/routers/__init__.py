"""Package for holding all routers"""

from typing import Final

from fastapi import APIRouter

from resource_server.routers.url_prefixes import URLPrefix
from resource_server.routers.users import USERS
from resource_server.routers.animes import ANIMES
from resource_server.routers.forums import FORUMS
from resource_server.routers.posts import POSTS
from resource_server.routers.comments import COMMENTS
from resource_server.routers.misc import MISC

type t_route_prefixes = tuple[tuple[APIRouter, tuple[URLPrefix, ...]], ...]

ROUTER_PREFIXES: Final[t_route_prefixes] = tuple(
    (
        (USERS, (URLPrefix.USERS,)),
        (ANIMES, (URLPrefix.ANIMES,)),
        (FORUMS, (URLPrefix.FORUNS,)),
        (POSTS, (URLPrefix.POSTS,)),
        (COMMENTS, (URLPrefix.POSTS, URLPrefix.COMMENTS)),
        (MISC, ()),
    )
)
