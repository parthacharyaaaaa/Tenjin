from enum import Enum

__all__ = ("URLPrefix",)


class URLPrefix(str, Enum):
    ANIMES = "animes"
    COMMENTS = "comments"
    FORUNS = "forums"
    MISC = ""
    USERS = "users"
    POSTS = "posts"
