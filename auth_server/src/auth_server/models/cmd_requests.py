from typing import Annotated

from pydantic import BaseModel, Field

from auth_server.models.auth_requests import AuthenticationModel, username_annotation


class AdminAuthenticationModel(AuthenticationModel):
    identity: username_annotation


class AdminDeletionModel(BaseModel):
    id_: Annotated[int, Field(ge=1, frozen=True, alias="id")]


class AdminRefreshModel(BaseModel):
    refresh_digest: Annotated[str, Field(frozen=True, alias="refresh-digest")]
