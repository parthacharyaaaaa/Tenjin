from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from resource_server.models.annotations import (
    username_annotation,
    user_password_annotation,
    email_annotation,
    strong_entity_pk_annotation,
    forum_name_annotation,
    forum_description_annotation,
    post_title_annotation,
    post_body_annotation,
    post_report_tag_annotation,
    post_report_description_annotation,
)
from resource_server.config.database_constants import (
    CommentConstants,
    UserTicketConstants,
)
from resource_server.models.database import AdminRoles


class UserTicketModel(BaseModel):
    email: email_annotation
    description: Annotated[
        str,
        BeforeValidator(lambda x: x.strip()),
        Field(
            frozen=True,
            min_length=UserTicketConstants.DESCRIPTION_MIN_LENGTH,
            max_length=UserTicketConstants.DESCRIPTION_MAX_LENGTH,
        ),
    ]


class ForumCreationModel(BaseModel):
    title: forum_name_annotation
    description: forum_description_annotation
    parent_anime_id: Annotated[int, Field(ge=1, frozen=True)]


class ForumUpdationModel(BaseModel):
    title: forum_name_annotation | None
    description: forum_description_annotation | None

    @model_validator(mode="after")
    def validate_non_emptiness(self) -> Self:
        if not (self.title or self.description):
            raise ValueError("At least one of title or description required")
        return self


class GenericAdminModel(BaseModel):
    user_id: strong_entity_pk_annotation


class AdminAddModel(GenericAdminModel):
    role: Annotated[
        Literal[AdminRoles.SUPER, AdminRoles.ADMIN],
        BeforeValidator(lambda x: x.strip().upper()),
    ]


class PostCreationModel(BaseModel):
    forum_id: strong_entity_pk_annotation
    title: post_title_annotation
    body: post_body_annotation


class PostAmendmentModel(BaseModel):
    closed: bool | None = None
    title: post_title_annotation | None = None
    body: post_body_annotation | None = None

    @model_validator(mode="after")
    def validate_non_emptiness(self) -> Self:
        if not (self.closed or self.title or self.body):
            raise ValueError("Empty request provided for post amendment")
        return self


class ReportModel(BaseModel):
    tag: post_report_tag_annotation
    description: post_report_description_annotation


class VoteModel(BaseModel):
    vote: Annotated[Literal[1, -1], Field(frozen=True)]


class CommentModel(BaseModel):
    body: Annotated[
        str,
        BeforeValidator(lambda x: x.strip()),
        Field(
            max_length=CommentConstants.COMMENT_MAX_LENGTH,
            min_length=CommentConstants.COMMENT_MIN_LENGTH,
            frozen=True,
        ),
    ]

    client_tag: Annotated[str | None, Field(frozen=True, default=None)]


class UserCreationModel(BaseModel):
    username: username_annotation
    email: email_annotation
    password: user_password_annotation


class GenericUserIdentificationModel(BaseModel):
    identity: username_annotation | email_annotation


class UserLoginModel(GenericUserIdentificationModel):
    password: user_password_annotation
