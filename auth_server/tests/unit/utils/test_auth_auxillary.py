from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from auth_server.utils.auth_auxillary import attach_tokens, report_suspicious_activity
from fastapi.datastructures import URL
from fastapi.responses import JSONResponse


def _normalize_raw_cookies(cookies: list[bytes]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for i in cookies:
        processed_i = i.split(b"=")
        assert len(processed_i) <= 2, "Invalid cookie syntax"
        if len(processed_i) == 1:
            d[processed_i[0].decode("utf-8")] = True
        else:
            d[processed_i[0].decode("utf-8")] = processed_i[1].decode("utf-8")
    return d


def common_cookie_assertions(cookie_dict: dict[str, Any]) -> None:
    assert "HttpOnly" in cookie_dict
    assert cookie_dict["SameSite"] == "lax"


def test_attach_tokens_on_request(dummy_json_response: JSONResponse) -> None:
    access_token, refresh_token = "access", "refresh"
    refresh_max_age = access_max_age = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    cookie_paths: tuple[URL, ...] = tuple(map(URL, ("/foo", "/bar")))
    attach_tokens(
        dummy_json_response,
        access_token,
        refresh_token,
        access_max_age,
        refresh_max_age,
        cookie_paths,
    )

    access_cookie_data: list[bytes] | None = None
    refresh_cookie_data: list[list[bytes]] = []

    for header in dummy_json_response.headers.raw:
        if header[0] == b"set-cookie":
            if header[1].startswith(b"access=access"):
                access_cookie_data = header[1].split(b"; ")
                continue
            if header[1].startswith(b"refresh=refresh"):
                refresh_cookie_data.append(header[1].split(b"; "))
    assert access_cookie_data, "Missing access token"
    assert len(refresh_cookie_data) == 2, "Invalid refresh token count"

    print(access_cookie_data)
    normalized_access_cookies = _normalize_raw_cookies(access_cookie_data)
    common_cookie_assertions(normalized_access_cookies)
    assert int(normalized_access_cookies["Max-Age"]) == access_max_age
    assert normalized_access_cookies["Path"] == "/"

    normalized_refresh_cookie_data = tuple(
        _normalize_raw_cookies(i) for i in refresh_cookie_data
    )
    assert (
        normalized_refresh_cookie_data[0]["Max-Age"]
        == normalized_refresh_cookie_data[1]["Max-Age"]
    )
    assert int(normalized_refresh_cookie_data[0]["Max-Age"]) == refresh_max_age
    assert (
        normalized_refresh_cookie_data[0]["Path"] == "/bar"
        and normalized_refresh_cookie_data[1]["Path"] == "/foo"
    ) or (
        normalized_refresh_cookie_data[0]["Path"] == "/foo"
        and normalized_refresh_cookie_data[1]["Path"] == "/bar"
    )


class AsyncContext:
    def __init__(self) -> None:
        self.enter = AsyncMock(return_value=None)
        self.exit = AsyncMock(return_value=None)

    async def __aenter__(self) -> None:
        await self.enter()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.exit(exc_type, exc_value, traceback)


@dataclass
class ActivityHarness:
    config: SimpleNamespace
    suspicious_repository: Mock
    admin_repository: Mock
    coordinator: Mock
    session_manager: Mock
    context: AsyncContext
    calls: Mock


@pytest.fixture
def activity_harness() -> ActivityHarness:
    calls = Mock()
    suspicious_repository = Mock()
    admin_repository = Mock()
    coordinator = Mock()
    session_manager = Mock()
    context = AsyncContext()

    suspicious_repository.insert_activity = AsyncMock()
    suspicious_repository.get_activity_log = AsyncMock()
    admin_repository.set_admin_locked = AsyncMock()
    coordinator.multirepo_work_context = Mock(return_value=context)
    session_manager.get_admin_session_via_admin_id = AsyncMock()
    session_manager.terminate_session_via_object = AsyncMock()

    calls.attach_mock(suspicious_repository.insert_activity, "insert")
    calls.attach_mock(coordinator.multirepo_work_context, "work_context")
    calls.attach_mock(context.enter, "enter")
    calls.attach_mock(suspicious_repository.get_activity_log, "get_log")
    calls.attach_mock(admin_repository.set_admin_locked, "lock_admin")
    calls.attach_mock(
        session_manager.get_admin_session_via_admin_id,
        "get_session",
    )
    calls.attach_mock(
        session_manager.terminate_session_via_object,
        "terminate_session",
    )
    calls.attach_mock(context.exit, "exit")

    config = SimpleNamespace(
        ADMIN=SimpleNamespace(
            MAX_ACTIVITY_LIMIT=3,
            SUSPICIOUS_LOOKBACK_TIME=timedelta(minutes=10),
        )
    )
    return ActivityHarness(
        config,
        suspicious_repository,
        admin_repository,
        coordinator,
        session_manager,
        context,
        calls,
    )


def activity(time_logged: datetime) -> SimpleNamespace:
    return SimpleNamespace(time_logged=time_logged)


async def report(
    harness: ActivityHarness,
    *,
    force_logout: bool = True,
) -> None:
    await report_suspicious_activity(
        harness.config,  # pyrefly: ignore[bad-argument-type]
        7,
        "suspicious action",  # type: ignore[arg-type]
        harness.suspicious_repository,
        harness.admin_repository,
        harness.coordinator,
        harness.session_manager,
        force_logout=force_logout,
    )


@pytest.mark.asyncio
async def test_report_records_and_reads_activity_when_logout_is_disabled(
    activity_harness: ActivityHarness,
) -> None:
    activity_harness.suspicious_repository.get_activity_log.return_value = []

    await report(activity_harness, force_logout=False)

    assert activity_harness.calls.mock_calls == [
        call.insert(7, "suspicious action"),
        call.work_context(
            activity_harness.suspicious_repository,
            activity_harness.admin_repository,
        ),
        call.enter(),
        call.get_log(7, 3),
        call.exit(None, None, None),
    ]


@pytest.mark.parametrize(
    "oldest_activity_time",
    (
        datetime(2026, 1, 1, 11, 50, tzinfo=UTC),
        datetime(2026, 1, 1, 11, 49, 59, tzinfo=UTC),
    ),
)
@pytest.mark.asyncio
async def test_report_does_not_lock_for_activity_outside_lookback_window(
    activity_harness: ActivityHarness,
    oldest_activity_time: datetime,
) -> None:
    current_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    activity_harness.suspicious_repository.get_activity_log.return_value = [
        activity(oldest_activity_time)
    ]

    with patch("auth_server.utils.auth_auxillary.datetime") as datetime_mock:
        datetime_mock.now.return_value = current_time
        await report(activity_harness)

    activity_harness.admin_repository.set_admin_locked.assert_not_awaited()
    activity_harness.session_manager.get_admin_session_via_admin_id.assert_not_awaited()
    activity_harness.context.exit.assert_awaited_once_with(None, None, None)


@pytest.mark.parametrize(
    "activities",
    (
        [activity(datetime(2026, 1, 1, 11, 50, 1, tzinfo=UTC))],
        [activity(datetime(2026, 1, 1, 10, minute, tzinfo=UTC)) for minute in range(4)],
    ),
    ids=("within-lookback", "over-activity-limit"),
)
@pytest.mark.parametrize(
    "existing_session", (None, object()), ids=("no-session", "session")
)
@pytest.mark.asyncio
async def test_report_locks_admin_and_terminates_existing_session_in_order(
    activity_harness: ActivityHarness,
    activities: list[SimpleNamespace],
    existing_session: object | None,
) -> None:
    current_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    activity_harness.suspicious_repository.get_activity_log.return_value = activities
    activity_harness.session_manager.get_admin_session_via_admin_id.return_value = (
        existing_session
    )

    with patch("auth_server.utils.auth_auxillary.datetime") as datetime_mock:
        datetime_mock.now.return_value = current_time
        await report(activity_harness)

    expected_calls = [
        call.insert(7, "suspicious action"),
        call.work_context(
            activity_harness.suspicious_repository,
            activity_harness.admin_repository,
        ),
        call.enter(),
        call.get_log(7, 3),
        call.lock_admin(7, locked=True),
        call.get_session(7),
    ]
    if existing_session is not None:
        expected_calls.append(call.terminate_session(existing_session))
    expected_calls.append(call.exit(None, None, None))
    assert activity_harness.calls.mock_calls == expected_calls


@pytest.mark.asyncio
async def test_report_handles_empty_activity_log_without_locking(
    activity_harness: ActivityHarness,
) -> None:
    activity_harness.suspicious_repository.get_activity_log.return_value = []

    await report(activity_harness)

    activity_harness.admin_repository.set_admin_locked.assert_not_awaited()
    activity_harness.session_manager.get_admin_session_via_admin_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_unwinds_work_context_when_activity_lookup_fails(
    activity_harness: ActivityHarness,
) -> None:
    error = RuntimeError("lookup failed")
    activity_harness.suspicious_repository.get_activity_log.side_effect = error

    with pytest.raises(RuntimeError, match="lookup failed"):
        await report(activity_harness)

    activity_harness.context.exit.assert_awaited_once()
    assert activity_harness.context.exit.await_args.args[:2] == (RuntimeError, error)  # pyrefly: ignore
    activity_harness.admin_repository.set_admin_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_stops_after_admin_lock_failure(
    activity_harness: ActivityHarness,
) -> None:
    current_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    activity_harness.suspicious_repository.get_activity_log.return_value = [
        activity(current_time)
    ]
    error = RuntimeError("lock failed")
    activity_harness.admin_repository.set_admin_locked.side_effect = error

    with (
        patch("auth_server.utils.auth_auxillary.datetime") as datetime_mock,
        pytest.raises(RuntimeError, match="lock failed"),
    ):
        datetime_mock.now.return_value = current_time
        await report(activity_harness)

    activity_harness.session_manager.get_admin_session_via_admin_id.assert_not_awaited()
    activity_harness.context.exit.assert_awaited_once()
    assert activity_harness.context.exit.await_args.args[:2] == (RuntimeError, error)  # pyrefly: ignore
