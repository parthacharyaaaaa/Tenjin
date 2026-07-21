import weakref
from typing import Any, TypeVar

__all__ = ("SingletonMetaclass",)

T = TypeVar("T")

# NOTE: Use of getattr and setattr is to avoid
# type checker's complaining about reportAttributeAccessIssue


class SingletonMetaclass(type):
    _instance: weakref.ReferenceType[Any] | None = None

    def __call__(cls: type[T], *args: Any, **kwds: Any) -> T:
        if attr := getattr(cls, "_instance", None):
            return attr()

        temp_instance: T = super().__call__(*args, **kwds)
        setattr(
            cls,
            "_instance",
            weakref.ref(temp_instance, lambda _: setattr(cls, "_instance", None)),
        )

        return getattr(cls, "_instance")()
