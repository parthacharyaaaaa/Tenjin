from auxillary.dependencies.indicator import Inject
from auxillary.dependencies.resolution import inject_worker_dependencies
from typing_extensions import Annotated


def test_dependency_injection() -> None:
    int_injection_value, str_injection_value = 1, "1"
    int_annotation = Annotated[int, Inject(lambda: int_injection_value)]
    str_annotation = Annotated[str, Inject(lambda: str_injection_value)]

    def dummy_di_subject(x: int_annotation, y: str_annotation):
        assert x == int_injection_value
        assert y == str_injection_value

    partial_di_subject = inject_worker_dependencies(dummy_di_subject)
    partial_di_subject()
