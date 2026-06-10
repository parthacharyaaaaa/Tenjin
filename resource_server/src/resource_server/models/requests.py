from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

from resource_server.models.annotations import (
    email_annotation,
    forum_name_annotation,
    forum_description_annotation,
)
from resource_server.config.database_constants import UserTicketConstants


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
