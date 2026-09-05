from datetime import timedelta


def milliseconds_to_timedelta_validator(ms: int) -> timedelta:
    if ms < 0:
        raise ValueError("Negative time value")
    return timedelta(milliseconds=ms)
