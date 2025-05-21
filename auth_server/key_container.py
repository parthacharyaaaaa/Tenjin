from dataclasses import dataclass, field
import time
import re

@dataclass(slots=True)
class KeyMetadata:
    '''Container to hold a key's data'''
    PUBLIC_PEM: bytes
    PRIVATE_PEM: bytes
    ALGORITHM: str
    EPOCH: float = field(default_factory=time.time)
    _ROTATED_AT: float|None = field(default=None, repr=False)

    @property
    def ROTATED_AT(self) -> float|None:
        return self._ROTATED_AT
    
    @ROTATED_AT.setter
    def ROTATED_AT(self, rotationTime: float) -> None:
        if not (rotationTime and rotationTime > self.EPOCH):
            raise ValueError('Invalid rotation time')
        self._ROTATED_AT = rotationTime
