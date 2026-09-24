from collections.abc import Mapping
from typing import Any

from fastapi.exceptions import StarletteHTTPException

from auxillary.data_structures.response import HypermediaResponseSequence


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
