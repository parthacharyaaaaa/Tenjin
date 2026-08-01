class StatusController:
    __slots__ = ("status_ok",)

    def __init__(self) -> None:
        self.status_ok = True


class StatusProxy:
    __slots__ = ("_status_controller",)

    def __init__(self, status_controller: StatusController) -> None:
        self._status_controller = status_controller

    @property
    def status_ok(self) -> bool:
        return self._status_controller.status_ok
