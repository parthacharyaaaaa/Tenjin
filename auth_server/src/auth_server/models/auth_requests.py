from typing import Annotated, Final, TypeAlias
from pydantic import BaseModel, BeforeValidator, Field

from auth_server.config import constants
from auth_server.config.utils import IDENTITY_PATTERN, EMAIL_PATTERN

type username_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        min_length=constants.MIN_IDENTITY_LENGTH,
        max_length=constants.MAX_IDENTITY_LENGTH,
        frozen=True,
        pattern=IDENTITY_PATTERN,
    ),
]

type email_annotation = Annotated[
    str,
    BeforeValidator(lambda x: x.strip()),
    Field(
        min_length=constants.MIN_EMAIL_LENGTH,
        max_length=constants.MAX_EMAIL_LENGTH,
        pattern=EMAIL_PATTERN,
    ),
]


class AuthenticationModel(BaseModel):
    identity: username_annotation | email_annotation
    password: Annotated[
        str,
        BeforeValidator(lambda x: x.strip()),
        Field(
            min_length=constants.MIN_PASSWORD_LENGTH,
            max_length=constants.MAX_PASSWORD_LENGTH,
            frozen=True,
        ),
    ]


class RegistrationModel(AuthenticationModel):
    identity: username_annotation
    email: email_annotation
