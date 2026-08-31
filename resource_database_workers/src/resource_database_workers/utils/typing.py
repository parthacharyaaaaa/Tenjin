from typing import Any, ClassVar, Literal, Protocol

type t_action_literal = Literal["save", "vote", "subscribe"]


class GenericDataclass(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]
