from dataclasses import dataclass
from datetime import UTC, datetime
from types import NoneType
from typing import ClassVar

import pytest
from auxillary.data_structures.dto import AbstractResult


@dataclass(slots=True, init=False)
class ExampleResult(AbstractResult):
    resource_name: ClassVar[str] = "example"

    id_: int
    created_at: datetime
    active: bool
    tags: list
    metadata: dict
    absent: NoneType
    _private: str


def make_result() -> ExampleResult:
    result = ExampleResult()
    result.id_ = 7
    result.created_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    result.active = True
    result.tags = ["one", "two"]
    result.metadata = {"kind": "example"}
    result.absent = None
    result._private = "hidden"
    return result


def test_result_subclass_requires_resource_name() -> None:
    with pytest.raises(ValueError, match="Missing class variable: resource_name"):

        class MissingResourceName(AbstractResult):
            pass


def test_construct_from_cache_sets_known_fields_only() -> None:
    result = ExampleResult.construct_from_cache(
        {
            "id_": 7,
            "active": True,
            "unknown": "ignored",
        }
    )

    assert result.id_ == 7
    assert result.active is True
    assert not hasattr(result, "unknown")


def test_construct_from_orm_sets_matching_columns_only() -> None:
    class Columns:
        @staticmethod
        def keys() -> tuple[str, ...]:
            return ("active", "created_at", "unknown")

    class Table:
        columns = Columns()

    class ORMObject:
        __table__ = Table()
        active = False
        created_at = datetime(2026, 2, 3, tzinfo=UTC)
        unknown = "ignored"

    result = ExampleResult.construct_from_orm(ORMObject())

    assert result.active is False
    assert result.created_at == datetime(2026, 2, 3, tzinfo=UTC)
    assert not hasattr(result, "unknown")


def test_json_representation_maps_json_types_and_hides_private_fields() -> None:
    result = make_result()

    assert result.__json_repr__() == {
        "id": 7,
        "created_at": "2026-01-02T03:04:00+00:00",
        "active": True,
        "tags": ["one", "two"],
        "metadata": {"kind": "example"},
        "absent": None,
    }


def test_cache_representation_maps_cache_types_and_hides_private_fields() -> None:
    result = make_result()

    assert result.__cache_repr__() == {
        "id": 7,
        "created_at": "2026-01-02T03:04:00+00:00",
        "active": 1,
        "tags": "['one', 'two']",
        "metadata": "{'kind': 'example'}",
        "absent": "",
    }
