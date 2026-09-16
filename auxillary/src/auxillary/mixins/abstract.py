class StrictAbstractMixin:
    """
    Mixin class for disallowing instantiation of abstract classes entirely,
    without the narrower runtime condition of overriding abstract methods
    (see https://docs.python.org/3/library/abc.html#abc.abstractmethod)

    **Usage**
    Inherit from this mixin class, and pass the keyword-only arg `abstract` as True
    e.g. class Foo(StrictAbstractMixin, abstract=True): ...
    """

    __abstract__ = True

    def __init_subclass__(cls, *, abstract=False, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.__abstract__ = abstract

    def __new__(cls, *args, **kwargs):
        if cls.__abstract__:
            raise TypeError(f"Cannot instantiate abstract class {cls.__name__!r}")
        return super().__new__(cls)
