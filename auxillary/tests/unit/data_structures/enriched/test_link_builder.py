from http import HTTPMethod
from unittest.mock import Mock

import pytest
from auxillary.data_structures.enriched.link_builder import HypermediaLinkBuilder
from fastapi import Request
from fastapi.datastructures import URL


@pytest.mark.parametrize(
    ("absolute", "expected_href"),
    (
        (False, "/items/7?page=2"),
        (True, "https://example.test/items/7?page=2"),
    ),
)
def test_link_self_selects_relative_or_absolute_url(
    absolute: bool, expected_href: str
) -> None:
    request = Mock(spec=Request)
    request.url = URL("https://example.test/items/7?page=2")
    request.method = "GET"

    link = HypermediaLinkBuilder(request, absolute=absolute).link_self()

    assert link.href == expected_href
    assert link.rel == "_self"
    assert link.type is HTTPMethod.GET


def test_link_builds_path_parameters_and_expands_query_sequences() -> None:
    request = Mock(spec=Request)
    request.url_for.return_value = URL("https://example.test/items/7")
    builder = HypermediaLinkBuilder(request)

    link = builder.link(
        "item",
        "details",
        HTTPMethod.POST,
        item_id=7,
        query={"tag": ["one", "two"], "page": 3, "cursor": None},
    )

    request.url_for.assert_called_once_with("item", item_id="7")
    assert link.href == "/items/7?tag=one&tag=two&page=3"
    assert link.rel == "details"
    assert link.type is HTTPMethod.POST
