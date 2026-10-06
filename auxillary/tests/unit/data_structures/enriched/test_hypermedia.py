from http import HTTPMethod

from auxillary.data_structures.enriched.hypermedia import HypermediaResponse


def test_hypermedia_response_strips_link_whitespace() -> None:
    response = HypermediaResponse(
        href="  /items/7?view=full  ",
        rel="  item  ",
        type=HTTPMethod.GET,
    )

    assert response.href == "/items/7?view=full"
    assert response.rel == "item"
