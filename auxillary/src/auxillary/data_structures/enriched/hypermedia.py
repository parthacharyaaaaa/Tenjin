from http import HTTPMethod
from typing import Annotated

from pydantic import BaseModel
from pydantic.functional_validators import BeforeValidator

_whitespace_stripped_string = Annotated[str, BeforeValidator(lambda x: x.strip())]


class HypermediaResponse(BaseModel):
    href: _whitespace_stripped_string
    rel: _whitespace_stripped_string
    type: HTTPMethod


class HypermediaResponseSequence(BaseModel):
    links: list[HypermediaResponse]
