from typing import Annotated

from pydantic import BaseModel, Field

from auth_server.config.constants import MAX_IDENTITY_LENGTH, MIN_IDENTITY_LENGTH
from auth_server.models.auth_requests import AuthenticationModel, username_annotation


class AdminAuthenticationModel(AuthenticationModel):
    identity: username_annotation


class AdminIdentificationModel(BaseModel):
    username: Annotated[
        str,
        Field(
            min_length=MIN_IDENTITY_LENGTH, max_length=MAX_IDENTITY_LENGTH, frozen=True
        ),
    ]


class AdminRefreshModel(BaseModel):
    refresh_digest: Annotated[str, Field(frozen=True, alias="refresh-digest")]
