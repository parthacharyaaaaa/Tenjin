"""Package for holding all blueprints"""

from .blueprint_animes import ANIMES_BLUEPRINT
from .blueprint_comments import COMMENTS_BLUEPRINT
from .blueprint_forum import FORUMS_BLUEPRINT
from .blueprint_misc import MISC_BLUEPRINT
from .blueprint_posts import POSTS_BLUEPRINT
from .blueprint_user import USERS_BLUEPRINT
from .url_prefixes import URLPrefixes

__all__ = ("ANIMES_BLUEPRINT",
           "COMMENTS_BLUEPRINT",
           "FORUMS_BLUEPRINT",
           "MISC_BLUEPRINT",
           "POSTS_BLUEPRINT",
           "USERS_BLUEPRINT",
           "URLPrefixes",)
