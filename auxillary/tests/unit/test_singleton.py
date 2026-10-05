from auxillary.singleton import SingletonMetaclass


class SingletonChild(metaclass=SingletonMetaclass):
    pass


def test_singleton_metaclass() -> None:
    # class_ref = SingletonMetaclass("Foo", (), {})
    instance_ref = SingletonChild()

    duplicate_instance_ref = SingletonChild()
    assert instance_ref is duplicate_instance_ref, (
        f"Singleton assertion failed, original reference: {id(instance_ref)}, duplicate: {id(duplicate_instance_ref)}"
    )
