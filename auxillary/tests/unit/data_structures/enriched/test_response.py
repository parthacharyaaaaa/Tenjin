from http import HTTPMethod

import orjson
from auxillary.data_structures.enriched.hypermedia import (
    HypermediaResponse,
    HypermediaResponseSequence,
)
from auxillary.data_structures.enriched.response import EnrichedJSONResponse


def test_response_keeps_mapping_content_unwrapped() -> None:
    response = EnrichedJSONResponse({"id": 7})

    assert orjson.loads(response.body) == {"id": 7}


def test_response_wraps_non_mapping_content() -> None:
    response = EnrichedJSONResponse([1, 2], array_key="items")

    assert orjson.loads(response.body) == {"items": [1, 2]}


def test_response_adds_hypermedia_using_custom_key() -> None:
    hypermedia = HypermediaResponseSequence(
        links=[
            HypermediaResponse(
                href="/items/7",
                rel="item",
                type=HTTPMethod.GET,
            )
        ]
    )
    response = EnrichedJSONResponse(
        {"id": 7},
        hypermedia_data=hypermedia,
        hypermedia_links_key="links",
    )

    assert orjson.loads(response.body) == {
        "id": 7,
        "links": [{"href": "/items/7", "rel": "item", "type": "GET"}],
    }
