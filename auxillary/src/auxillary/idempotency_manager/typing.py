from datetime import timedelta
from typing import Any, Protocol


class IdempotencyManager(Protocol):
    async def check_idempotency(self, identifier: Any) -> bool: ...

    async def set_idempotent_entry(
        self, identifier: str, *, duration_s: timedelta | None = None
    ) -> bool: ...
