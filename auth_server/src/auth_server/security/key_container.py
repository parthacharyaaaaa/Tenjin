import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class KeyMetadata:
    """Container to hold a key's data"""

    PUBLIC_PEM: bytes
    PRIVATE_PEM: bytes
    ALGORITHM: str
    EPOCH: float = field(default_factory=time.time)
    _ROTATED_AT: float | None = field(default=None, repr=False)

    @property
    def ROTATED_AT(self) -> float | None:   # noqa: N802
        return self._ROTATED_AT

    @ROTATED_AT.setter
    def ROTATED_AT(self, rotation_time: float) -> None:     # noqa: N802
        if not (rotation_time and rotation_time > self.EPOCH):
            raise ValueError("Invalid rotation time")
        self._ROTATED_AT = rotation_time
