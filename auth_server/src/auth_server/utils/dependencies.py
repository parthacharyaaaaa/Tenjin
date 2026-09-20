from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request

from auth_server.dependencies import get_admin_session_manager
from auth_server.models.session import AdminSession
from auth_server.security.admin_roles import ROLE_PERMISSIONS
from auth_server.security.admin_sessions import AdminSessionManager
from auth_server.security.permissions import Permission
from auth_server.strings import AdminStrings


async def get_admin_session(
    request: Request,
    admin_session_manager: Annotated[
        AdminSessionManager, Depends(get_admin_session_manager)
    ],
) -> AdminSession:
    session_id: Final[str | None] = request.headers.get(
        AdminStrings.SESSION_TOKEN_HEADER
    )
    if session_id is None:
        raise HTTPException(
            401, f"Missing session token: {AdminStrings.SESSION_TOKEN_HEADER}"
        )

    try:
        admin_session: (
            AdminSession | None
        ) = await admin_session_manager.get_admin_session(session_id)
    except ValueError as e:
        raise HTTPException(401, e.args[0] if e.args else "Invalid session") from e
    except Exception as e:
        raise HTTPException(
            500, f"Failed to fetch session information for session with ID {session_id}"
        ) from e
    if not admin_session:
        raise HTTPException(401, f"No session with ID {session_id} found")

    return admin_session


def require_permissions(*required_permissions: Permission):
    async def closure(
        admin_session: Annotated[AdminSession, Depends(get_admin_session)],
    ) -> AdminSession:
        missing: set[Permission] = set(required_permissions) - set(
            ROLE_PERMISSIONS[admin_session.role]
        )
        if missing:
            raise HTTPException(
                403, f"Missing permissions for: {', '.join(m.value for m in missing)}"
            )
        return admin_session

    return closure
