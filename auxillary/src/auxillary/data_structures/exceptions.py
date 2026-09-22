from collections.abc import Mapping
from http import HTTPMethod
from typing import Annotated, Any

from fastapi.exceptions import StarletteHTTPException
from pydantic import BaseModel
from pydantic.functional_validators import BeforeValidator

_whitespace_stripped_string = Annotated[str, BeforeValidator(lambda x: x.strip())]


class HypermediaResponse(BaseModel):
    href: _whitespace_stripped_string
    rel: _whitespace_stripped_string
    type: HTTPMethod


class HypermediaResponseSequence(BaseModel):
    links: list[HypermediaResponse]


class EnrichedHTTPException(StarletteHTTPException):
    __slots__ = ("hypermedia", "additional_fields")

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        headers: Mapping[str, str] | None = None,
        *,
        hypermedia: HypermediaResponseSequence | None = None,
        additional_fields: Mapping[str, str | int | bool] | None = None,
    ) -> None:
        self.hypermedia = hypermedia
        self.additional_fields = additional_fields
        super().__init__(status_code=status_code, detail=detail, headers=headers)
