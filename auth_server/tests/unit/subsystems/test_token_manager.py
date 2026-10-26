from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import jwt.exceptions as jwt_exceptions
import pytest
from auth_server.datastructures.exceptions.token import (
    ImmatureRefreshAttemptError,
    InvalidKeyError,
    InvalidTokenError,
    MissingTokenFamilyError,
    ReplayedTokenError,
    TokenFamilyExistsError,
)
from auth_server.models.data_transport.keydata import ActiveKeyPrivateDataResult
from auth_server.subsystems.token_manager import TokenManager
from auth_server.tokens.typing import TokenType
from sqlalchemy.util.typing import Final


class AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value
        self.enter = AsyncMock(return_value=value)
        self.exit = AsyncMock(return_value=None)

    async def __aenter__(self) -> object:
        return await self.enter()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.exit(exc_type, exc_value, traceback)


@dataclass
class TokenManagerHarness:
    manager: TokenManager
    token_store: Mock
    synced_store: Mock
    repository: Mock
    synced_state: Mock
    logger: Mock
    pipeline: Mock
    pipeline_context: AsyncContext
    redis_calls: Mock


@pytest.fixture
def token_manager_harness() -> TokenManagerHarness:
    token_store = Mock()
    synced_store = Mock()
    repository = Mock()
    synced_state = Mock()
    logger = Mock()
    pipeline = Mock()
    pipeline_context = AsyncContext(pipeline)
    redis_calls = Mock()

    token_store.lindex = AsyncMock()
    token_store.lrange = AsyncMock(return_value=[])
    token_store.llen = AsyncMock()
    token_store.rpop = AsyncMock()
    token_store.delete = AsyncMock()
    token_store.pipeline = Mock(return_value=pipeline_context)
    pipeline.lpush = Mock(return_value=pipeline)
    pipeline.expireat = Mock(return_value=pipeline)
    pipeline.rpop = Mock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[])
    repository.get_keydata = AsyncMock()
    synced_state.get_jwks = AsyncMock()

    redis_calls.attach_mock(token_store.lindex, "lindex")
    redis_calls.attach_mock(token_store.lrange, "lrange")
    redis_calls.attach_mock(token_store.llen, "llen")
    redis_calls.attach_mock(token_store.rpop, "rpop")
    redis_calls.attach_mock(token_store.delete, "delete")
    redis_calls.attach_mock(token_store.pipeline, "pipeline")
    redis_calls.attach_mock(pipeline_context.enter, "enter")
    redis_calls.attach_mock(pipeline.lpush, "lpush")
    redis_calls.attach_mock(pipeline.expireat, "expireat")
    redis_calls.attach_mock(pipeline.rpop, "rpop")
    redis_calls.attach_mock(pipeline.execute, "execute")
    redis_calls.attach_mock(pipeline_context.exit, "exit")

    key_config = SimpleNamespace(SIGNATURE_ALGORITHM="ES256")
    token_config = SimpleNamespace(
        REFRESH_LIFETIME=timedelta(hours=1),
        ACCESS_LIFETIME=timedelta(minutes=5),
        LEEWAY=timedelta(seconds=30),
        MAX_TOKENS_PER_FAMILY=3,
        POLL_INTERVAL=timedelta(seconds=15),
    )

    manager = TokenManager(
        token_store,
        synced_store,
        repository,
        key_config,  # type: ignore
        token_config,  # type: ignore
        logger=logger,
        universal_claims={"iss": "tenjin"},
        universal_headers={"aud": "admins"},
    )

    return TokenManagerHarness(
        manager,
        token_store,
        synced_store,
        repository,
        synced_state,
        logger,
        pipeline,
        pipeline_context,
        redis_calls,
    )


@pytest.fixture
def signing_manager(token_manager_harness: TokenManagerHarness) -> TokenManager:
    manager = token_manager_harness.manager
    manager._active_key = "key-1"
    manager._active_key_private_pem = b"private-pem"
    manager._key_mapping = {
        "key-1": SimpleNamespace(public_pem=b"public-pem", alg="ES256")  # type: ignore
    }
    return manager


def test_post_init_sets_headers_and_schedules_polling(
    token_manager_harness: TokenManagerHarness,
) -> None:
    harness = token_manager_harness

    assert harness.manager.universal_headers == {
        "aud": "admins",
        "typ": "JWT",
        "alg": "ES256",
    }


@pytest.mark.asyncio
async def test_transactional_block_keeps_successful_changes(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager

    async with manager.transactional_block():
        manager._key_mapping["new"] = Mock()
        manager._active_key = "new"
        manager._active_key_private_pem = b"new-private"

    assert set(manager._key_mapping) == {"new"}
    assert manager._active_key == "new"
    assert manager._active_key_private_pem == b"new-private"


@pytest.mark.asyncio
async def test_transactional_block_restores_all_key_state_on_error(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    original_key = Mock()
    manager._key_mapping = {"original": original_key}
    manager._active_key = "original"
    manager._active_key_private_pem = b"original-private"

    with pytest.raises(RuntimeError, match="failed"):
        async with manager.transactional_block():
            manager._key_mapping["new"] = Mock()
            manager._key_mapping.pop("original")
            manager._active_key = "new"
            manager._active_key_private_pem = b"new-private"
            raise RuntimeError("failed")

    print(manager._key_mapping, manager._active_key)
    assert manager._key_mapping == {"original": original_key}
    assert manager._active_key == "original"
    assert manager._active_key_private_pem == b"original-private"


@pytest.mark.parametrize("include_existing_mapping", (False, True))
def test_set_key_state_replaces_mapping_and_selects_active_key(
    token_manager_harness: TokenManagerHarness,
    include_existing_mapping: bool,
) -> None:
    manager = token_manager_harness.manager
    public_copy = Mock()
    active_key = SimpleNamespace(
        kid="active",
        private_pem=b"private",
        create_public_copy=Mock(return_value=public_copy),
    )
    existing = {"verification": Mock()} if include_existing_mapping else None

    manager.set_key_state(active_key, existing)  # type: ignore[arg-type]

    assert manager._key_mapping == (existing or {}) | {"active": public_copy}
    assert manager._active_key == "active"
    assert manager._active_key_private_pem == b"private"


@pytest.mark.parametrize(
    ("token_type", "decoded"),
    (
        (
            TokenType.StandardAccess,
            {"iat": 1, "exp": 2, "fid": "family", "sub": "7", "sid": 3, "jti": "jti"},
        ),
        (
            TokenType.StandardRefresh,
            {
                "iat": 1,
                "exp": 2,
                "nbf": 1,
                "fid": "family",
                "sub": "7",
                "sid": 3,
                "jti": "jti",
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_decode_token_uses_selected_verification_key(
    signing_manager: TokenManager,
    token_type: TokenType,
    decoded: dict[str, object],
) -> None:
    with (
        patch(
            "auth_server.subsystems.token_manager.jwt.get_unverified_header",
            return_value={"kid": "key-1"},
        ),
        patch(
            "auth_server.subsystems.token_manager.jwt.decode",
            return_value=decoded,
        ) as decode,
    ):
        assert await signing_manager.decode_token("token", token_type) == decoded

    decode.assert_called_once_with(
        jwt="token",
        key=b"public-pem",
        algorithms=["ES256"],
        leeway=timedelta(seconds=30),
        options=None,
    )


@pytest.mark.asyncio
async def test_decode_token_rejects_unknown_key(signing_manager: TokenManager) -> None:
    with patch(
        "auth_server.subsystems.token_manager.jwt.get_unverified_header",
        return_value={"kid": "unknown"},
    ):
        with pytest.raises(InvalidKeyError, match="key is not recognised"):
            await signing_manager.decode_token("token")


@pytest.mark.asyncio
async def test_decode_token_wraps_missing_key_id(signing_manager: TokenManager) -> None:
    with patch(
        "auth_server.subsystems.token_manager.jwt.get_unverified_header",
        return_value={},
    ):
        with pytest.raises(
            InvalidTokenError,
            match="Token headers missing key ID",
        ):
            await signing_manager.decode_token("token")


@pytest.mark.parametrize(
    ("error", "expected_error", "message"),
    (
        (
            jwt_exceptions.ImmatureSignatureError("immature"),
            ImmatureRefreshAttemptError,
            "Premature token reissuance",
        ),
        (
            jwt_exceptions.InvalidIssuedAtError("issued-at"),
            InvalidTokenError,
            "Invalid token, please login again",
        ),
        (
            jwt_exceptions.InvalidIssuerError("issuer"),
            InvalidTokenError,
            "Invalid token, please login again",
        ),
    ),
)
@pytest.mark.parametrize("token_type", tuple(TokenType))
@pytest.mark.asyncio
async def test_decode_token_normalizes_time_and_issuer_errors(
    signing_manager: TokenManager,
    error: Exception,
    expected_error: type[Exception],
    message: str,
    token_type: TokenType,
) -> None:
    with (
        patch(
            "auth_server.subsystems.token_manager.jwt.get_unverified_header",
            return_value={"kid": "key-1"},
        ),
        patch(
            "auth_server.subsystems.token_manager.jwt.decode",
            side_effect=error,
        ),
    ):
        with pytest.raises(expected_error, match=message):
            await signing_manager.decode_token("token", token_type)


@pytest.mark.asyncio
async def test_reissue_token_pair_runs_refresh_flow_in_order(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    calls = Mock()
    decoded = {
        "iat": 1,
        "exp": 500,
        "nbf": 2,
        "fid": "family",
        "sub": "7",
        "sid": 3,
        "jti": "old-jti",
    }
    decode = AsyncMock(return_value=decoded)
    validate = AsyncMock()
    issue_pair = AsyncMock(return_value=("new-refresh", "new-access"))
    calls.attach_mock(decode, "decode")
    calls.attach_mock(validate, "validate")
    calls.attach_mock(issue_pair, "issue_pair")

    with (
        patch.object(TokenManager, "decode_token", decode),
        patch.object(TokenManager, "validate_reissuance_integrity", validate),
        patch.object(TokenManager, "_issue_token_pair", issue_pair),
    ):
        assert await manager.reissue_token_pair("old-refresh") == (
            "new-refresh",
            "new-access",
        )

    assert calls.mock_calls == [
        call.decode("old-refresh", token_type=TokenType.StandardRefresh),
        call.validate("family", "old-jti"),
        call.issue_pair("7", 3, family_id="family"),
    ]


@pytest.mark.parametrize(
    ("history", "jti", "expected_error", "message"),
    (
        ([], "old-jti", MissingTokenFamilyError, "Family ID family not found"),
        (
            ["latest", "old-jti"],
            "old-jti",
            ReplayedTokenError,
            "Replayed refresh token",
        ),
        (
            ["latest", "known-old"],
            "unknown",
            InvalidTokenError,
            "Unrecognized token for family family",
        ),
    ),
)
@pytest.mark.asyncio
async def test_validate_reissuance_integrity_rejects_invalid_history(
    token_manager_harness: TokenManagerHarness,
    history: list[str],
    jti: str,
    expected_error: type[Exception],
    message: str,
) -> None:
    manager = token_manager_harness.manager
    token_manager_harness.token_store.lrange.return_value = history

    with pytest.raises(expected_error, match=message):
        await manager.validate_reissuance_integrity("family", jti)

    token_manager_harness.token_store.lrange.assert_awaited_once_with("family", 0, -1)


@pytest.mark.asyncio
async def test_validate_reissuance_integrity_accepts_latest_jti(
    token_manager_harness: TokenManagerHarness,
) -> None:
    token_manager_harness.token_store.lrange.return_value = ["latest", "older"]

    await token_manager_harness.manager.validate_reissuance_integrity(
        "family", "latest"
    )


@pytest.mark.asyncio
async def test_issue_fresh_token_pair_rejects_existing_family(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    family_id = manager.generate_family_list_id("7")
    token_manager_harness.token_store.llen.return_value = 1

    with pytest.raises(TokenFamilyExistsError, match=f"Token family {family_id}"):
        await manager.issue_fresh_token_pair("7", 3)

    token_manager_harness.token_store.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_issue_refresh_token_writes_family_metadata_before_encoding(
    token_manager_harness: TokenManagerHarness,
    signing_manager: TokenManager,
) -> None:
    harness = token_manager_harness
    harness.token_store.llen.return_value = 1
    with (
        patch("auth_server.subsystems.token_manager.time.time", return_value=1000),
        patch.object(
            TokenManager,
            "generate_unique_identifier",
            return_value="new-jti",
        ),
        patch(
            "auth_server.subsystems.token_manager.jwt.encode",
            return_value="encoded-refresh",
        ) as encode,
    ):
        result = await signing_manager.issue_refresh_token(
            "7",
            3,
            additional_claims={"custom": True},
        )
    expected_family_id: Final[str] = signing_manager.generate_family_list_id("7")
    assert result == "encoded-refresh"
    assert harness.redis_calls.mock_calls == [
        call.llen(expected_family_id),
        call.pipeline(transaction=True),
        call.enter(),
        call.lpush(expected_family_id, "new-jti"),
        call.expireat(expected_family_id, 4600),
        call.execute(),
        call.exit(None, None, None),
    ]
    encode.assert_called_once_with(
        payload={
            "iat": 1000,
            "exp": 4600,
            "nbf": 1270,
            "fid": expected_family_id,
            "sub": "7",
            "sid": 3,
            "jti": "new-jti",
            "iss": "tenjin",
            "custom": True,
        },
        key=b"private-pem",
        algorithm="ES256",
        headers={"aud": "admins", "typ": "JWT", "alg": "ES256", "kid": "key-1"},
    )


@pytest.mark.asyncio
async def test_issue_fresh_token_pair_validates_then_issues_pair(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    validate = AsyncMock()
    issue_pair = AsyncMock(return_value=("refresh", "access"))

    with (
        patch.object(TokenManager, "validate_fresh_issuance_integrity", validate),
        patch.object(TokenManager, "_issue_token_pair", issue_pair),
    ):
        assert await manager.issue_fresh_token_pair("7", 3) == (
            "refresh",
            "access",
        )

    validate.assert_awaited_once_with(manager.generate_family_list_id("7"))
    issue_pair.assert_awaited_once_with("7", 3)


def test_issue_access_token_builds_and_encodes_payload(
    signing_manager: TokenManager,
) -> None:
    with (
        patch("auth_server.subsystems.token_manager.time.time", return_value=1000),
        patch.object(TokenManager, "generate_unique_identifier", return_value="jti"),
        patch(
            "auth_server.subsystems.token_manager.jwt.encode",
            return_value="encoded-access",
        ) as encode,
    ):
        result = signing_manager.issue_access_token(
            "7",
            3,
            family_id="family",
            additional_claims={"custom": True},
        )

    assert result == "encoded-access"
    encode.assert_called_once_with(
        payload={
            "iat": 1000,
            "exp": 1300,
            "fid": "family",
            "sub": "7",
            "sid": 3,
            "jti": "jti",
            "iss": "tenjin",
            "custom": True,
        },
        key=b"private-pem",
        algorithm="ES256",
        headers={"aud": "admins", "typ": "JWT", "alg": "ES256", "kid": "key-1"},
    )


@pytest.mark.parametrize("stored_family", ([], ["token"]))
@pytest.mark.asyncio
async def test_invalidate_family_deletes_existing_or_logs_missing(
    token_manager_harness: TokenManagerHarness,
    stored_family: list[str],
) -> None:
    harness = token_manager_harness
    harness.token_store.lrange.return_value = stored_family

    await harness.manager.invalidate_family("family")

    harness.token_store.lrange.assert_awaited_once_with("family", 0, -1)
    if stored_family:
        harness.token_store.delete.assert_awaited_once_with("family")
        harness.logger.info.assert_not_called()
    else:
        harness.token_store.delete.assert_not_awaited()
        harness.logger.info.assert_called_once_with("No Family Found")


@pytest.mark.asyncio
async def test_invalidate_family_wraps_store_errors(
    token_manager_harness: TokenManagerHarness,
) -> None:
    token_manager_harness.token_store.lrange.side_effect = OSError("redis failed")

    with pytest.raises(RuntimeError, match="Failed to perform operation"):
        await token_manager_harness.manager.invalidate_family("family")


def test_update_signing_key_selects_private_key_and_registers_public_copy(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    manager._active_key = "old"
    manager._active_key_private_pem = b"old-private"
    keydata = ActiveKeyPrivateDataResult(
        kid="new",
        alg="ES256",
        curve="secp256r1",
        epoch=datetime(2026, 1, 1, tzinfo=UTC),
        public_pem=b"public",
        rotated_out_at=None,
        expired_at=None,
        manual_rotation=None,
        rotated_by=None,
        private_pem=b"new-private",
    )

    manager.update_signing_key(keydata)

    assert manager._key_mapping["new"] == keydata.create_public_copy()
    assert manager._active_key == "new"
    assert manager._active_key_private_pem == b"new-private"


def test_update_verification_key_does_not_replace_signing_key(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    manager._active_key = "active"
    manager._active_key_private_pem = b"active-private"
    keydata = SimpleNamespace(kid="rotated")

    manager.update_verification_key(keydata)  # type: ignore[arg-type]

    assert manager._key_mapping["rotated"] is keydata
    assert manager._active_key == "active"
    assert manager._active_key_private_pem == b"active-private"


def test_invalidate_key_rejects_active_key(
    token_manager_harness: TokenManagerHarness,
) -> None:
    manager = token_manager_harness.manager
    manager._active_key = "active"
    manager._key_mapping = {"active": Mock()}

    with pytest.raises(RuntimeError, match="Cannot invalidate active signing key"):
        manager.invalidate_key("active")

    assert "active" in manager._key_mapping


@pytest.mark.parametrize("present", (False, True))
def test_invalidate_key_silently_removes_non_active_key(
    token_manager_harness: TokenManagerHarness,
    present: bool,
) -> None:
    manager = token_manager_harness.manager
    manager._active_key = "active"
    manager._key_mapping = {"other": Mock()} if present else {}

    manager.invalidate_key("other")

    assert "other" not in manager._key_mapping


def test_generate_unique_identifier_returns_uuid_hex() -> None:
    generated_uuid = SimpleNamespace(hex="unique-id")

    with patch(
        "auth_server.subsystems.token_manager.uuid.uuid4",
        return_value=generated_uuid,
    ):
        assert TokenManager.generate_unique_identifier() == "unique-id"
