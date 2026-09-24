from typing import Annotated, Final

from auxillary.data_structures.enriched.exceptions import EnrichedHTTPException
from auxillary.utils import json_repr
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from auth_server.admin.permissions import Permission
from auth_server.dependencies.local import (
    get_key_lifecycle_manager,
    get_keydata_repository,
    get_synced_store_key_state_manager,
)
from auth_server.dependencies.requests import require_permissions
from auth_server.keys.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.models.session import AdminSession
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)

KEY: Final[APIRouter] = APIRouter()


@KEY.get("/keys/{kid}")
async def get_key(
    kid: str,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.READ_KEY))
    ],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    public: bool = True,
) -> JSONResponse:
    try:
        key: (
            KeyPublicDataResult | KeyPrivateDataResult | None
        ) = await keydata_repository.get_keydata(kid, public_only=public)

        if not key:
            raise EnrichedHTTPException(404, "No key with this ID found")
    except SQLAlchemyError:
        raise Exception("Failed to fetch key")

    return JSONResponse(json_repr(key))


@KEY.delete("/keys/{kid}")
async def invalidate_key(
    kid: str,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.INVALIDATE_KEY))
    ],
    key_lifecycle_manager: Annotated[
        KeyLifecycleManager, Depends(get_key_lifecycle_manager)
    ],
    synced_keystate_manager: Annotated[
        SyncedStoreKeyStateManager, Depends(get_synced_store_key_state_manager)
    ],
) -> JSONResponse:
    additional_kw: dict[str, str] = {}
    await key_lifecycle_manager.invalidate_key(
        kid, intermediate_message_mapping=additional_kw
    )

    valid_keys: list[str] = await synced_keystate_manager.get_valid_keys_ids()
    return JSONResponse(
        {
            "message": "Key invalidated successfully",
            "purged_kid": kid,
            "valid_keys": valid_keys,
            "additional_info": additional_kw,
        }
    )


@KEY.delete("/keys/clean")
async def clean_keystore(
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.INVALIDATE_KEY))
    ],
    key_lifecycle_manager: Annotated[
        KeyLifecycleManager, Depends(get_key_lifecycle_manager)
    ],
) -> JSONResponse:
    active_key_id, invalidated_key_ids = await key_lifecycle_manager.clean_keystore()
    return JSONResponse(
        {
            "message": "All inactive keys have been invalidated",
            "invalidated keys": invalidated_key_ids,
            "active_key": active_key_id,
        }
    )


@KEY.post("/keys/rotate")
async def rotate_keys(
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.ROTATE_KEY))
    ],
    key_lifecycle_manager: Annotated[
        KeyLifecycleManager, Depends(get_key_lifecycle_manager)
    ],
    synced_key_state_manager: Annotated[
        SyncedStoreKeyStateManager, Depends(get_synced_store_key_state_manager)
    ],
) -> JSONResponse:
    previous_active_key_id: Final[
        str
    ] = await synced_key_state_manager.get_active_key_id()
    active_key: Final[KeyPublicDataResult] = await key_lifecycle_manager.rotate_key(
        rotation_author=admin_session.admin_id
    )
    return JSONResponse(
        {
            "message": "Key rotation successful",
            "active_key_data": json_repr(active_key),
            "previous_kid": previous_active_key_id,
        },
        status_code=201,
    )
