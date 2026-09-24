from collections.abc import Mapping
from typing import Annotated, Any, ClassVar

import orjson
from fastapi.responses import JSONResponse
from pydantic.functional_validators import BeforeValidator
from starlette.background import BackgroundTask

from auxillary.data_structures.enriched.hypermedia import HypermediaResponseSequence

_whitespace_stripped_string = Annotated[str, BeforeValidator(lambda x: x.strip())]


class EnrichedJSONResponse(JSONResponse):
    __slots__ = ("hypermedia_data", "array_key", "hypermedia_links_key")
    default_hypermedia_links_key: ClassVar[str] = "_links"
    default_response_key: ClassVar[str] = "response"

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
        hypermedia_data: HypermediaResponseSequence | None = None,
        *,
        array_key: str | None = None,
        hypermedia_links_key: str | None = None,
    ) -> None:
        self.hypermedia_data = hypermedia_data
        self.array_key = array_key
        self.hypermedia_links_key = hypermedia_links_key
        super().__init__(content, status_code, headers, media_type, background)

    def render(self, content: Any) -> bytes:
        if not isinstance(content, dict):
            content = {self.array_key or self.default_response_key: content}
        if self.hypermedia_data:
            content[self.hypermedia_links_key or self.default_hypermedia_links_key] = [
                link.model_dump() for link in self.hypermedia_data.links
            ]

        return orjson.dumps(content)
