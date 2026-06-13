from typing import Annotated

from pydantic import Field, BeforeValidator

from resource_server.config.constants import EMAIL_PATTERN
from resource_server.config.database_constants import (
    UserConstants,
    ForumConstants,
    PostConstants,
)
from resource_server.models.database_enums import ReportTags

type email_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        pattern=EMAIL_PATTERN,
        max_length=UserConstants.EMAIL_MAX_LENGTH,
        min_length=UserConstants.EMAIL_MIN_LENGTH,
        frozen=True,
    ),
]

type username_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        max_length=UserConstants.USERNAME_MAX_LENGTH,
        min_length=UserConstants.USERNAME_MIN_LENGTH,
        frozen=True,
    ),
]

generic_identity_annotation = username_annotation | email_annotation

type forum_name_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        min_length=ForumConstants.TITLE_MIN_LENGTH,
        max_length=ForumConstants.TITLE_MAX_LENGTH,
        frozen=True,
    ),
]

type forum_description_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        min_length=ForumConstants.DESCRIPTION_MIN_LENGTH,
        max_length=ForumConstants.DESCRIPTION_MAX_LENGTH,
        frozen=True,
    ),
]

type strong_entity_pk_annotation = Annotated[int, Field(ge=1, frozen=True)]

type post_body_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(max_length=PostConstants.BODY_MAX_LENGTH, frozen=True),
]

type post_title_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        max_length=PostConstants.TITLE_MAX_LENGTH,
        min_length=PostConstants.TITLE_MIN_LENGTH,
        frozen=True,
    ),
]

type post_report_tag_annotation = Annotated[
    ReportTags, BeforeValidator(lambda x: x.strip()), Field(frozen=True)
]

type post_report_description_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        max_length=PostConstants.REPORT_DESCRIPTION_MAX_LENGTH,
        min_length=PostConstants.REPORT_DESCRIPTION_MIN_LENGTH,
        frozen=True,
    ),
]

type user_password_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        max_length=UserConstants.PASSWORD_MAX_LENGTH,
        min_length=UserConstants.PASSWORD_MIN_LENGTH,
        frozen=True,
    ),
]
