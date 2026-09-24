from collections.abc import Sequence
from dataclasses import dataclass, field
from http import HTTPMethod
from typing import Any

from fastapi.datastructures import URL
from fastapi.requests import Request
from starlette.datastructures import QueryParams

from auxillary.data_structures.enriched.hypermedia import HypermediaResponse


@dataclass(frozen=True, slots=True)
class HypermediaLinkBuilder:
    request: Request
    absolute: bool = False
    self_referral_key: str = field(kw_only=True, default="_self")

    def link_self(self) -> HypermediaResponse:
        url: URL = self.request.url

        if self.absolute:
            href = str(url)
        else:
            href = url.path
            if url.query:
                href = f"{href}?{url.query}"

        return HypermediaResponse(
            href=href,
            rel=self.self_referral_key,
            type=HTTPMethod(self.request.method),
        )

    def link(
        self,
        route_name: str,
        rel: str,
        method: HTTPMethod = HTTPMethod.GET,
        *,
        query: dict[str, Any] | None = None,
        **path_parameters: Any,
    ) -> HypermediaResponse:
        url: URL = self.request.url_for(
            route_name,
            **{name: str(value) for name, value in path_parameters.items()},
        )

        if query:
            query_items: list[tuple[str, str]] = []
            for name, value in query.items():
                if value is None:
                    continue
                if isinstance(value, Sequence) and not isinstance(
                    value, (str, bytes, bytearray)
                ):
                    query_items.extend((name, str(item)) for item in value)
                    continue
                query_items.append((name, str(value)))
            url = url.replace(query=str(QueryParams(query_items)))

        if self.absolute:
            href = str(url)
        else:
            href = url.path
            if url.query:
                href = f"{href}?{url.query}"

        return HypermediaResponse(
            href=href,
            rel=rel,
            type=method,
        )
