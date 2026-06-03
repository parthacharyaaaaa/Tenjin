"""Package for holding all blueprints"""

from types import MappingProxyType
from typing import Final

from flask import Blueprint

from .blueprint_animes import ANIMES_BLUEPRINT
from .blueprint_comments import COMMENTS_BLUEPRINT
from .blueprint_forum import FORUMS_BLUEPRINT
from .blueprint_misc import MISC_BLUEPRINT
from .blueprint_posts import POSTS_BLUEPRINT
from .blueprint_user import USERS_BLUEPRINT
from .url_prefixes import URLPrefixes

__all__ = (
    "ANIMES_BLUEPRINT",
    "COMMENTS_BLUEPRINT",
    "FORUMS_BLUEPRINT",
    "MISC_BLUEPRINT",
    "POSTS_BLUEPRINT",
    "USERS_BLUEPRINT",
    "URLPrefixes",
    "PREFIX_MAPPING",
)

PREFIX_MAPPING: Final[MappingProxyType[Blueprint, URLPrefixes]] = MappingProxyType(
    {
        USERS_BLUEPRINT: URLPrefixes.USERS,
        COMMENTS_BLUEPRINT: URLPrefixes.COMMENTS,
        FORUMS_BLUEPRINT: URLPrefixes.FORUNS,
        MISC_BLUEPRINT: URLPrefixes.MISC,
        POSTS_BLUEPRINT: URLPrefixes.POSTS,
        ANIMES_BLUEPRINT: URLPrefixes.ANIMES,
    }
)
