from datetime import UTC, datetime
from typing import Annotated, ClassVar

import orjson
import pytest
from auxillary.data_structures.dto import AbstractResult
from pydantic import Field, PrivateAttr, ValidationError


class ExampleResult(AbstractResult):
    resource_name: ClassVar[str] = "example"

    id_: Annotated[int, Field(gt=0)]
    created_at: datetime
    active: bool
    tags: list[str]
    metadata: dict[str, int]
    absent: str | None = None
    _private: str = PrivateAttr(default="hidden")


def make_result() -> ExampleResult:
    return ExampleResult(
        id_=7,
        created_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        active=True,
        tags=["one", "two"],
        metadata={"count": 2},
    )


def make_cache_mapping() -> dict[str, bytes]:
    return {
        "id_": orjson.dumps(7),
        "created_at": orjson.dumps("2026-01-02T03:04:00Z"),
        "active": orjson.dumps(True),
        "tags": orjson.dumps(["one", "two"]),
        "metadata": orjson.dumps({"count": 2}),
        "absent": orjson.dumps(None),
    }


def test_result_subclass_requires_resource_name() -> None:
    with pytest.raises(ValueError, match="Missing class variable: resource_name"):

        class MissingResourceName(AbstractResult):
            value: int


def test_construct_from_cache_deserializes_redis_hash() -> None:
    result = ExampleResult.construct_from_cache(make_cache_mapping())

    assert result == make_result()


def test_construct_from_cache_accepts_decoded_redis_values() -> None:
    cache_mapping = {
        key: value.decode("utf-8") for key, value in make_cache_mapping().items()
    }

    assert ExampleResult.construct_from_cache(cache_mapping) == make_result()


def test_cache_representation_round_trip() -> None:
    result = make_result()

    assert ExampleResult.construct_from_cache(result.__cache_repr__()) == result


def test_construct_from_cache_ignores_extra_fields() -> None:
    cache_mapping = make_cache_mapping() | {"unknown": orjson.dumps("ignored")}

    result = ExampleResult.construct_from_cache(cache_mapping)

    assert result == make_result()
    assert not hasattr(result, "unknown")


def test_construct_from_cache_rejects_missing_required_fields() -> None:
    cache_mapping = make_cache_mapping()
    del cache_mapping["id_"]

    with pytest.raises(ValidationError) as exc_info:
        ExampleResult.construct_from_cache(cache_mapping)

    assert exc_info.value.errors()[0]["loc"] == ("id_",)
    assert exc_info.value.errors()[0]["type"] == "missing"


def test_construct_from_cache_rejects_malformed_json() -> None:
    cache_mapping = make_cache_mapping() | {"active": b"not-json"}

    with pytest.raises(orjson.JSONDecodeError):
        ExampleResult.construct_from_cache(cache_mapping)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("id_", "not-an-integer"),
        ("created_at", "not-a-datetime"),
        ("active", "not-a-boolean"),
        ("tags", {"not": "a-list"}),
        ("metadata", ["not", "a-mapping"]),
    ),
)
def test_construct_from_cache_rejects_invalid_field_types(
    field_name: str, invalid_value: object
) -> None:
    cache_mapping = make_cache_mapping() | {field_name: orjson.dumps(invalid_value)}

    with pytest.raises(ValidationError) as exc_info:
        ExampleResult.construct_from_cache(cache_mapping)

    assert exc_info.value.errors()[0]["loc"] == (field_name,)


def test_construct_from_cache_rejects_invalid_field_value() -> None:
    cache_mapping = make_cache_mapping() | {"id_": orjson.dumps(0)}

    with pytest.raises(ValidationError) as exc_info:
        ExampleResult.construct_from_cache(cache_mapping)

    assert exc_info.value.errors()[0]["loc"] == ("id_",)
    assert exc_info.value.errors()[0]["type"] == "greater_than"


def test_construct_from_cache_uses_pydantic_coercion() -> None:
    cache_mapping = make_cache_mapping() | {"id_": orjson.dumps("7")}

    result = ExampleResult.construct_from_cache(cache_mapping)

    assert result.id_ == 7
    assert isinstance(result.id_, int)


def test_construct_from_orm_reads_attributes_and_ignores_extras() -> None:
    class ORMObject:
        id_ = 7
        created_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        active = True
        tags = ["one", "two"]
        metadata = {"count": 2}
        absent = None
        unknown = "ignored"

    result = ExampleResult.construct_from_orm(ORMObject())  # type: ignore[arg-type]

    assert result == make_result()
    assert not hasattr(result, "unknown")


def test_construct_from_orm_rejects_missing_attributes() -> None:
    class IncompleteORMObject:
        id_ = 7

    with pytest.raises(ValidationError) as exc_info:
        ExampleResult.construct_from_orm(  # type: ignore[arg-type]
            IncompleteORMObject()
        )

    assert {error["loc"] for error in exc_info.value.errors()} == {
        ("created_at",),
        ("active",),
        ("tags",),
        ("metadata",),
    }


def test_json_representation_uses_json_types_and_hides_private_attributes() -> None:
    result = make_result()
    result._private = "still-hidden"

    assert result.__json_repr__() == {
        "id_": 7,
        "created_at": "2026-01-02T03:04:00Z",
        "active": True,
        "tags": ["one", "two"],
        "metadata": {"count": 2},
        "absent": None,
    }


def test_cache_representation_json_encodes_each_hash_field() -> None:
    result = make_result()
    result._private = "still-hidden"

    assert result.__cache_repr__() == make_cache_mapping()
