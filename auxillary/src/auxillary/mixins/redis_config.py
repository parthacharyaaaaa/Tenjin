from typing import Annotated, Any

from pydantic import Field


class BasicRedisConfigMixin:
    HOST: Annotated[str, Field(min_length=1)]
    PORT: Annotated[int, Field(le=65_535, ge=1024)]
    DB: Annotated[int, Field(default=0, ge=0)]
    DECODE_RESPONSES: Annotated[bool, Field(default=False)]

    def to_constructor_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.HOST,
            "port": self.PORT,
            "db": self.DB,
            "decode_responses": self.DECODE_RESPONSES,
        }
