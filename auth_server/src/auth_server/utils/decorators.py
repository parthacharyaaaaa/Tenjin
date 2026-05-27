from redis import Redis

from auth_server.dependencies import get_synced_store_client
from werkzeug.exceptions import Unauthorized, Forbidden
from flask import request, g
from functools import wraps
import time
import ujson
import base64
from typing import Any, Literal


def admin_only(
    required_role: Literal["staff", "super"] = "staff",
    synced_store_client: Redis = get_synced_store_client(),
):
    """
    #### Role-based admin session validation decorator.
    - Verifies token presence and semantics
    - Checks expiry
    - Validates session from SyncedStore
    - Compares role and ensures it meets or exceeds the required role

    On success, attaches session token to `g.SESSION_TOKEN`
    """
    role_hierarchy = {"staff": 1, "super": 2}

    def wrapper(endpoint):
        @wraps(endpoint)
        def decorated(*args, **kwargs):
            encodedSessionToken: str | None = request.headers.get(
                "X-SESSION-TOKEN", None
            )
            if not encodedSessionToken:
                raise Unauthorized("Missing session token")

            try:
                sessionToken: dict = ujson.loads(
                    base64.urlsafe_b64decode(encodedSessionToken).decode()
                )
            except Exception:
                raise Unauthorized("Malformed session token")

            sessionID = sessionToken.get("session_id")
            adminID = sessionToken.get("admin_id")
            expiry = sessionToken.get("expiry_at")
            role = sessionToken.get("role")
            iteration = sessionToken.get("session_iteration")

            if not adminID:
                raise Unauthorized("Invalid token")

            adminID = int(adminID)
            adminSessionKey = f"admin:{adminID}"

            if not (sessionID and expiry):
                synced_store_client.delete(adminSessionKey)
                report_suspicious_activity(
                    synced_store_client, adminID, "Invalid token submitted"
                )
                raise Unauthorized("Invalid token")

            if time.time() > expiry:
                synced_store_client.delete(adminSessionKey)
                raise Forbidden("Session expired, please login again")

            adminSessionMapping: dict[bytes, Any] = synced_store_client.hgetall(
                adminSessionKey
            )
            if not adminSessionMapping:
                report_suspicious_activity(
                    synced_store_client, adminID, "No active session found"
                )
                raise Unauthorized("No session for this admin exists")

            if not (
                sessionID == int(adminSessionMapping.get(b"session_id"))
                and expiry == float(adminSessionMapping.get(b"expiry_at"))
                and role == adminSessionMapping.get(b"role").decode()
                and iteration == int(adminSessionMapping.get(b"session_iteration"))
            ):
                report_suspicious_activity(
                    synced_store_client, adminID, "Invalid session token"
                )
                raise Unauthorized("Invalid session token")

            actual_role = role
            if role_hierarchy.get(actual_role, 0) < role_hierarchy[required_role]:
                raise Forbidden("Insufficient permissions for this action")

            g.SESSION_TOKEN = sessionToken
            return endpoint(*args, **kwargs)

        return decorated

    return wrapper
