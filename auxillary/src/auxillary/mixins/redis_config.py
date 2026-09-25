from typing import Annotated

from pydantic import Field


class BasicRedisConfigMixin:
    HOST: Annotated[str, Field(min_length=1, serialization_alias="host")]
    PORT: Annotated[int, Field(le=65_535, ge=1024, serialization_alias="port")]
    DB: Annotated[int, Field(default=0, ge=0, serialization_alias="db")]
    DECODE_RESPONSES: Annotated[
        bool, Field(default=False, serialization_alias="decode_responses")
    ]
