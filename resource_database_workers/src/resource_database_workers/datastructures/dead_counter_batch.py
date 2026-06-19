from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class DeadCounterBatch:
    table: str
    column: str
    counters: dict[int, int]
    failure_time: datetime = field(default_factory=datetime.now)
