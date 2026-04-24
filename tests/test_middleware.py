"""Tests for middleware exposure — builder + serve()/APCoreMCP integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apcore_mcp._utils import resolve_executor
from apcore_mcp.middleware_builder import build_middleware_from_config

# ---------------------------------------------------------------------------
# build_middleware_from_config — unit tests
# ---------------------------------------------------------------------------


def test_build_empty_returns_empty_list():
    assert build_middleware_from_config([]) == []


def test_build_retry_middleware_with_defaults():
    from apcore import RetryMiddleware

    result = build_middleware_from_config([{"type": "retry"}])
    assert len(result) == 1
    assert isinstance(result[0], RetryMiddleware)


def test_build_retry_middleware_with_custom_config():
    from apcore import RetryMiddleware

    result = build_middleware_from_config([{"type": "retry", "max_retries": 5, "base_delay_ms": 50}])
    assert len(result) == 1
    assert isinstance(result[0], RetryMiddleware)


def test_build_logging_middleware():
    from apcore import LoggingMiddleware

    result = build_middleware_from_config([{"type": "logging"}])
    assert len(result) == 1
    assert isinstance(result[0], LoggingMiddleware)


def test_build_error_history_middleware_with_shorthand_keys():
    from apcore import ErrorHistoryMiddleware

    result = build_middleware_from_config(
        [
            {
                "type": "error_history",
                "max_entries_per_module": 25,
                "max_total_entries": 500,
            }
        ]
    )
    assert len(result) == 1
    assert isinstance(result[0], ErrorHistoryMiddleware)


def test_build_multiple_in_order():
    from apcore import LoggingMiddleware, RetryMiddleware

    result = build_middleware_from_config([{"type": "retry"}, {"type": "logging"}])
    assert [type(m) for m in result] == [RetryMiddleware, LoggingMiddleware]


def test_build_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown type 'bogus'"):
        build_middleware_from_config([{"type": "bogus"}])


def test_build_missing_type_raises():
    with pytest.raises(ValueError, match="missing required 'type' key"):
        build_middleware_from_config([{"max_retries": 3}])


def test_build_non_mapping_entry_raises():
    with pytest.raises(ValueError, match="must be an object"):
        build_middleware_from_config(["retry"])  # type: ignore[list-item]


def test_build_error_history_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unexpected keys"):
        build_middleware_from_config([{"type": "error_history", "bogus_key": True}])


# ---------------------------------------------------------------------------
# resolve_executor — middleware wiring
# ---------------------------------------------------------------------------


class _Exec:
    """Pre-existing Executor stub: has call_async + use()."""

    def __init__(self):
        self.used: list[object] = []

    async def call_async(self, *_args, **_kwargs):
        return {}

    def use(self, mw):
        self.used.append(mw)
        return self


def test_middleware_applied_to_existing_executor():
    exc = _Exec()
    mw1, mw2 = MagicMock(), MagicMock()

    result = resolve_executor(exc, middleware=[mw1, mw2])

    assert result is exc
    assert exc.used == [mw1, mw2]


def test_middleware_none_means_no_use_calls():
    exc = _Exec()
    result = resolve_executor(exc)
    assert result is exc
    assert exc.used == []


def test_middleware_applied_to_new_executor():
    from apcore import Registry, RetryMiddleware

    registry = Registry()
    mw = RetryMiddleware()
    executor = resolve_executor(registry, middleware=[mw])

    # Verify the middleware was registered on the new Executor.
    assert mw in list(executor._middleware_manager._middlewares)


def test_middleware_empty_list_is_noop():
    exc = _Exec()
    resolve_executor(exc, middleware=[])
    assert exc.used == []


def test_middleware_on_executor_without_use_raises():
    class _ExecNoUse:
        async def call_async(self, *_args, **_kwargs):
            return {}

    exc = _ExecNoUse()
    with pytest.raises(RuntimeError, match="does not support .use"):
        resolve_executor(exc, middleware=[MagicMock()])


# ---------------------------------------------------------------------------
# serve() + APCoreMCP — Config Bus integration
# ---------------------------------------------------------------------------


def test_apcore_mcp_reads_config_bus_middleware():
    """APCoreMCP should load middleware from Config Bus `mcp.middleware`."""
    from apcore import Registry, RetryMiddleware

    from apcore_mcp.apcore_mcp import APCoreMCP

    registry = Registry()

    # Return key-specific values so mcp.acl / mcp.pipeline don't receive the
    # middleware list (which would trigger ValueError in build_acl_from_config).
    config_data = {"mcp.middleware": [{"type": "retry", "max_retries": 2}]}
    fake_config = MagicMock()
    fake_config.get.side_effect = lambda key, *a, **kw: config_data.get(key)

    with patch("apcore.Config.load", return_value=fake_config):
        mcp = APCoreMCP(registry)

    mws = list(mcp._executor._middleware_manager._middlewares)
    assert any(isinstance(m, RetryMiddleware) for m in mws)


def test_apcore_mcp_merges_config_and_constructor_middleware():
    """Config Bus and constructor middleware both end up installed.

    Execution order is determined by ``Middleware.priority`` (higher first) and
    insertion order within equal priorities — caller supplies one of each source
    and verifies both show up in the manager.
    """
    from apcore import LoggingMiddleware, Registry, RetryMiddleware

    from apcore_mcp.apcore_mcp import APCoreMCP

    registry = Registry()

    config_data = {"mcp.middleware": [{"type": "retry"}]}
    fake_config = MagicMock()
    fake_config.get.side_effect = lambda key, *a, **kw: config_data.get(key)

    user_mw = LoggingMiddleware()
    with patch("apcore.Config.load", return_value=fake_config):
        mcp = APCoreMCP(registry, middleware=[user_mw])

    mws = list(mcp._executor._middleware_manager._middlewares)
    assert any(isinstance(m, RetryMiddleware) for m in mws), "Config Bus retry missing"
    assert user_mw in mws, "Caller-supplied LoggingMiddleware missing"


def test_apcore_mcp_no_config_bus_still_works():
    """If Config.load() returns None, constructor still accepts middleware."""
    from apcore import Registry, RetryMiddleware

    from apcore_mcp.apcore_mcp import APCoreMCP

    registry = Registry()
    user_mw = RetryMiddleware()

    with patch("apcore.Config.load", return_value=None):
        mcp = APCoreMCP(registry, middleware=[user_mw])

    assert user_mw in list(mcp._executor._middleware_manager._middlewares)
