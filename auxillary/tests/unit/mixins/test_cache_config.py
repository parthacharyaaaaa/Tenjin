import pytest
from auxillary.mixins.cache_config import BasicCacheTTLConfig
from pydantic import BaseModel, ValidationError


class CacheTTLConfig(BasicCacheTTLConfig, BaseModel):
    pass


VALID_TTLS = {
    "TTL_CAP": 600,
    "TTL_STRONGEST": 500,
    "TTL_STRONG": 400,
    "TTL_WEAK": 300,
    "TTL_EPHEMERAL": 200,
    "TTL_PROMOTION": 100,
}


def test_cache_ttls_accept_descending_order() -> None:
    CacheTTLConfig(**VALID_TTLS)


def test_cache_ttls_reject_inconsistent_order() -> None:
    with pytest.raises(ValidationError, match="Cache TTL Values inconsistent"):
        CacheTTLConfig(**(VALID_TTLS | {"TTL_STRONG": 550}))
