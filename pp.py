from pydantic import ConfigDict, Field
from typing import Annotated


class AdminSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id_: Annotated[
        int, Field(ge=1, serialization_alias="id", default_factory=lambda: uuid4().int)
    ]
