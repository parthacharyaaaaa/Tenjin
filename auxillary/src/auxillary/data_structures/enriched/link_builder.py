from dataclasses import dataclass, field
from http import HTTPMethod
from typing import Any

from fastapi.datastructures import URL
from fastapi.requests import Request

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
            url: URL = url.include_query_params(
                **{
                    name: str(value)
                    for name, value in query.items()
                    if value is not None
                }
            )

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
