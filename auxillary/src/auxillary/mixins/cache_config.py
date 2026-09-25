from datetime import timedelta
from typing import Self

from pydantic import model_validator

from auxillary.mixins.annotations import timedelta_s


class BasicCacheTTLConfig:
    TTL_CAP: timedelta_s
    TTL_PROMOTION: timedelta_s
    TTL_STRONGEST: timedelta_s
    TTL_STRONG: timedelta_s
    TTL_WEAK: timedelta_s
    TTL_EPHEMERAL: timedelta_s

    @model_validator(mode="after")
    def validate_ttl_times(self) -> Self:
        time_dict: dict[str, timedelta] = {
            "maximum": self.TTL_CAP,
            "strongest": self.TTL_STRONGEST,
            "strong": self.TTL_STRONG,
            "weak": self.TTL_WEAK,
            "ephemeral": self.TTL_EPHEMERAL,
            "promotion": self.TTL_PROMOTION,
        }

        if sorted(time_dict.values(), reverse=True) != list(time_dict.values()):
            print(sorted(time_dict.values()), list(time_dict.values()))
            raise ValueError(
                " ".join(
                    (
                        "Cache TTL Values inconsistent, descending order:",
                        ", ".join(time_dict.keys()),
                        "got:",
                        ", ".join(f"{k}: {v}" for k, v in time_dict.items()),
                    )
                )
            )
        return self


class BasicNegativeCacheConfig:
    NF_SENTINEL_KEY: str
    NF_SENTINEL_VALUE: str

    @property
    def NF_MAPPING(self) -> dict[str, str]:  # noqa: N802
        return {self.NF_SENTINEL_KEY: self.NF_SENTINEL_VALUE}
