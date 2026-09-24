from http import HTTPMethod

from auxillary.data_structures.enriched.hypermedia import HypermediaResponseSequence
from auxillary.data_structures.enriched.link_builder import HypermediaLinkBuilder


def login_hypermedia(
    builder: HypermediaLinkBuilder,
    *,
    admin: bool = False,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link(
                "admin_login" if admin else "login",
                "login",
                HTTPMethod.POST,
            )
        ]
    )


def token_hypermedia(
    builder: HypermediaLinkBuilder,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link("reissue", "refresh", HTTPMethod.GET),
            builder.link("purge_family", "logout", HTTPMethod.DELETE),
        ]
    )


def admin_session_hypermedia(
    builder: HypermediaLinkBuilder,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link("admin_refresh", "refresh", HTTPMethod.POST),
            builder.link("admin_logout", "logout", HTTPMethod.PATCH),
        ]
    )


def admin_lock_hypermedia(
    builder: HypermediaLinkBuilder,
    *,
    locked: bool,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link(
                "admin_unlock" if locked else "admin_lock",
                "unlock" if locked else "lock",
                HTTPMethod.DELETE if locked else HTTPMethod.POST,
            )
        ]
    )


def jwks_hypermedia(
    builder: HypermediaLinkBuilder,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(links=[builder.link("jwks", "jwks")])


def key_hypermedia(
    builder: HypermediaLinkBuilder,
    kid: str,
    *,
    public: bool = True,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link(
                "get_key",
                "self",
                query=None if public else {"public": False},
                kid=kid,
            ),
            builder.link("jwks", "jwks"),
        ]
    )


def key_rotation_hypermedia(
    builder: HypermediaLinkBuilder,
    active_kid: str,
    previous_kid: str,
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(
        links=[
            builder.link("get_key", "active-key", kid=active_kid),
            builder.link("get_key", "previous-key", kid=previous_kid),
            builder.link("jwks", "jwks"),
        ]
    )
