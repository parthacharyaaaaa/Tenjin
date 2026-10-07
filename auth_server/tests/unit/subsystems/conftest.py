import sys
from types import ModuleType


def _load_key_manager_past_type_only_import_cycle() -> None:
    key_manager_name = "auth_server.subsystems.key_manager"
    token_manager_name = "auth_server.subsystems.token_manager"
    if key_manager_name in sys.modules or token_manager_name in sys.modules:
        return

    token_manager_stub = ModuleType(token_manager_name)
    token_manager_stub.TokenManager = type("TokenManager", (), {})  # type: ignore[attr-defined]
    sys.modules[token_manager_name] = token_manager_stub
    try:
        __import__(key_manager_name)
    finally:
        del sys.modules[token_manager_name]


_load_key_manager_past_type_only_import_cycle()
