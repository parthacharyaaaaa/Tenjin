from dataclasses import dataclass

from auth_server.models.session import AdminSession


@dataclass(frozen=True, slots=True)
class AdminContext:
    session_token: str
    session: AdminSession
