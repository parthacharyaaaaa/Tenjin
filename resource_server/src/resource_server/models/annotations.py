from typing import Annotated

from pydantic import Field, BeforeValidator

from resource_server.config.constants import EMAIL_PATTERN
from resource_server.config.database_constants import UserConstants

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
