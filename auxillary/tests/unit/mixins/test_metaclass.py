import pytest
from auxillary.mixins.metaclass import AntiSingletonMixin
from auxillary.singleton import SingletonMetaclass


def test_anti_singleton_rejects_singleton_metaclass() -> None:
    with pytest.raises(TypeError, match="Singleton metaclass detected"):

        class InvalidSingleton(AntiSingletonMixin, metaclass=SingletonMetaclass):
            pass
