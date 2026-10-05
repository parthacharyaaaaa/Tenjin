from datetime import timedelta

from auxillary.mixins.annotations import timedelta_days, timedelta_ms, timedelta_s
from pydantic import BaseModel


class TimeAnnotationsModel(BaseModel):
    milliseconds: timedelta_ms
    seconds: timedelta_s
    days: timedelta_days


def test_time_annotations_use_their_matching_converters() -> None:
    model = TimeAnnotationsModel(milliseconds=1, seconds=2, days=3)

    assert model.milliseconds == timedelta(milliseconds=1)
    assert model.seconds == timedelta(seconds=2)
    assert model.days == timedelta(days=3)
