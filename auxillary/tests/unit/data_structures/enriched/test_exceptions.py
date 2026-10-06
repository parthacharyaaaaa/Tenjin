from auxillary.data_structures.enriched.exceptions import EnrichedHTTPException
from auxillary.data_structures.enriched.hypermedia import (
    HypermediaResponse,
    HypermediaResponseSequence,
)


def test_enriched_http_exception_preserves_response_metadata() -> None:
    hypermedia = HypermediaResponseSequence(
        links=[HypermediaResponse(href="/items", rel="items", type="GET")]
    )
    exception = EnrichedHTTPException(
        404,
        "missing",
        {"X-Reason": "gone"},
        hypermedia=hypermedia,
        additional_fields={"resource_id": 7},
    )

    assert exception.status_code == 404
    assert exception.detail == "missing"
    assert exception.headers == {"X-Reason": "gone"}
    assert exception.hypermedia is hypermedia
    assert exception.additional_fields == {"resource_id": 7}
