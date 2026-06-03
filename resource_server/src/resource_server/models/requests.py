from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

from resource_server.models.annotations import email_annotation, username_annotation
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
