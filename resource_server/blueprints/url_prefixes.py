from enum import Enum

__all__ = ("URLPrefixes",)

class URLPrefixes(str, Enum):
    ANIMES = "animes"
    COMMENTS = "comments"
    FORUNS = "forums"
    MISC = ""
    USERS = "users"
    POSTS = "posts"
