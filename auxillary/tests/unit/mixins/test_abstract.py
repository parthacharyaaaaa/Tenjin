import pytest
from auxillary.mixins.abstract import StrictAbstractMixin


class AbstractExample(StrictAbstractMixin, abstract=True):
    pass


class ConcreteExample(AbstractExample):
    pass


def test_abstract_class_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError, match="Cannot instantiate abstract class"):
        AbstractExample()


def test_concrete_subclass_can_be_instantiated() -> None:
    assert isinstance(ConcreteExample(), ConcreteExample)
