from collections.abc import Callable
from datetime import timedelta

import pytest
from auxillary.mixins.validators import (
    days_to_timedelta_validator,
    milliseconds_to_timedelta_validator,
    seconds_to_timedelta_validator,
)


@pytest.mark.parametrize(
    ("validator", "value", "expected"),
    (
        (milliseconds_to_timedelta_validator, 1, timedelta(milliseconds=1)),
        (seconds_to_timedelta_validator, 2, timedelta(seconds=2)),
        (days_to_timedelta_validator, 3, timedelta(days=3)),
    ),
)
def test_time_validator_conversion(
    validator: Callable[[int], timedelta], value: int, expected: timedelta
) -> None:
    assert validator(value) == expected


@pytest.mark.parametrize(
    "validator",
    (
        milliseconds_to_timedelta_validator,
        seconds_to_timedelta_validator,
        days_to_timedelta_validator,
    ),
)
def test_time_validator_rejects_negative_values(
    validator: Callable[[int], timedelta],
) -> None:
    with pytest.raises(ValueError, match="Negative time value"):
        validator(-1)


@pytest.mark.parametrize(
    "validator",
    (
        milliseconds_to_timedelta_validator,
        seconds_to_timedelta_validator,
        days_to_timedelta_validator,
    ),
)
def test_time_validator_accepts_zero(
    validator: Callable[[int], timedelta],
) -> None:
    assert validator(0) == timedelta()
