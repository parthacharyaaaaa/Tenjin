from auxillary.singleton import SingletonMetaclass


class AntiSingletonMixin:
    def __init_subclass__(cls, /, **kwargs):
        super().__init_subclass__(**kwargs)
        if issubclass(type(cls), SingletonMetaclass):
            raise TypeError(
                f"Singleton metaclass detected for non-singleton "
                "enforced class {cls.__name__}"
            )
