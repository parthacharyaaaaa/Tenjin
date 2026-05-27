import weakref
from typing import Any

__all__ = ("SingletonMetaclass",)


class SingletonMetaclass(type):
    _instance: weakref.ReferenceType[Any] | None = None

    def __call__(cls, *args: Any, **kwds: Any) -> Any:
        if cls._instance:
            return cls._instance()

        temp_instance: Any = super().__call__(*args, **kwds)
        cls._instance = weakref.ref(
            temp_instance, lambda _: setattr(cls, "_instance", None)
        )
        return cls._instance()
