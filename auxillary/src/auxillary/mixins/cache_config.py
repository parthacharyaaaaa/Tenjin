from typing import Annotated, Self
from pydantic import Field, model_validator


class BasicCacheTTLConfig:
    TTL_CAP: Annotated[int, Field(ge=0)]
    TTL_PROMOTION: Annotated[int, Field(ge=0)]
    TTL_STRONGEST: Annotated[int, Field(ge=0)]
    TTL_STRONG: Annotated[int, Field(ge=0)]
    TTL_WEAK: Annotated[int, Field(ge=0)]
    TTL_EPHEMERAL: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_ttl_times(self) -> Self:
        time_dict: dict[str, int] = {
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
    def NF_MAPPING(self) -> dict[str, str]:
        return {self.NF_SENTINEL_KEY: self.NF_SENTINEL_VALUE}
