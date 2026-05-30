from typing import Any, Protocol

__all__ = ("SupportsJSON",)


class SupportsJSON(Protocol):
    def __json_like__(self) -> dict[str, Any]: ...
