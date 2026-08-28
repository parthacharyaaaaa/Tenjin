from typing import Any
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Inject:
    dependency: Callable[..., Any]
