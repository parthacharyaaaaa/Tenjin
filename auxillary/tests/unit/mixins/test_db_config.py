import pytest
from auxillary.mixins.db_config import BasicConnectionPoolConfigMixin
from pydantic import BaseModel, ValidationError


class ConnectionPoolConfig(BasicConnectionPoolConfigMixin, BaseModel):
    pass


def test_connection_pool_accepts_equal_minimum_and_maximum() -> None:
    ConnectionPoolConfig(CONNECTION_POOL_MIN_SIZE=2, CONNECTION_POOL_MAX_SIZE=2)


def test_connection_pool_rejects_minimum_greater_than_maximum() -> None:
    with pytest.raises(
        ValidationError,
        match="Connection pool min size 2 cannot be greater than max size 1",
    ):
        ConnectionPoolConfig(CONNECTION_POOL_MIN_SIZE=2, CONNECTION_POOL_MAX_SIZE=1)
