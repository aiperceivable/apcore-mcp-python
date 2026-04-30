"""Regression tests for D4-001, D9-001, D10-006, D9-004, D9-005, D9-006, D9-010, D11-019.

Each test must FAIL before the fix is applied and PASS after.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ISSUE 1/8 — D4-001: Missing LICENSE file
# ---------------------------------------------------------------------------


class TestLicenseFileExists:
    """D4-001 — LICENSE file must exist at repo root and contain 'Apache'."""

    def _repo_root(self) -> Path:
        # tests/ lives one level below the repo root
        return Path(__file__).parent.parent

    def test_license_file_exists(self) -> None:
        license_path = self._repo_root() / "LICENSE"
        assert license_path.exists(), "LICENSE file must exist at repo root"

    def test_license_contains_apache(self) -> None:
        license_path = self._repo_root() / "LICENSE"
        assert license_path.exists(), "LICENSE file missing"
        content = license_path.read_text()
        assert "Apache" in content, "LICENSE must contain 'Apache'"


# ---------------------------------------------------------------------------
# ISSUE 2/8 — D9-001: Parallel serve()/async_serve() pipelines
# ---------------------------------------------------------------------------


class TestServeAndClassMethodDelegateSameCodePath:
    """D9-001 — APCoreMCP.serve() and module-level serve() must share code path."""

    def _make_registry(self) -> MagicMock:
        m = MagicMock()
        m.list.return_value = []
        m.get_definition.return_value = None
        return m

    def test_apcore_mcp_serve_delegates_to_module_serve(self) -> None:
        """APCoreMCP.serve() must call the module-level serve() internally."""
        from apcore_mcp import serve as module_serve
        from apcore_mcp.apcore_mcp import APCoreMCP

        mock_registry = self._make_registry()

        mcp = APCoreMCP.__new__(APCoreMCP)
        mcp._registry = mock_registry
        mcp._executor = MagicMock()
        mcp._name = "test"
        mcp._version = "1.0"
        mcp._tags = None
        mcp._prefix = None
        mcp._validate_inputs = False
        mcp._output_formatter = None
        mcp._metrics_collector = None
        mcp._authenticator = None
        mcp._require_auth = True
        mcp._exempt_paths = None
        mcp._async_tasks = False
        mcp._async_max_concurrent = 10
        mcp._async_max_tasks = 1000
        mcp._async_bridge = None
        mcp._usage_collector = None

        called_with: list[dict] = []

        original_serve = module_serve

        def capturing_serve(*args, **kwargs):
            called_with.append({"args": args, "kwargs": kwargs})
            # Don't actually run asyncio.run
            raise SystemExit(0)

        with patch("apcore_mcp.apcore_mcp.serve", capturing_serve):
            try:
                mcp.serve(transport="stdio")
            except SystemExit:
                pass

        assert len(called_with) == 1, (
            "APCoreMCP.serve() must delegate to module-level serve(). "
            f"Called {len(called_with)} times."
        )

    async def test_apcore_mcp_async_serve_delegates_to_module_async_serve(self) -> None:
        """APCoreMCP.async_serve() must delegate to module-level async_serve()."""
        from apcore_mcp.apcore_mcp import APCoreMCP

        mock_registry = self._make_registry()

        mcp = APCoreMCP.__new__(APCoreMCP)
        mcp._registry = mock_registry
        mcp._executor = MagicMock()
        mcp._name = "test"
        mcp._version = "1.0"
        mcp._tags = None
        mcp._prefix = None
        mcp._validate_inputs = False
        mcp._output_formatter = None
        mcp._metrics_collector = None
        mcp._authenticator = None
        mcp._require_auth = True
        mcp._exempt_paths = None
        mcp._async_tasks = False
        mcp._async_max_concurrent = 10
        mcp._async_max_tasks = 1000
        mcp._async_bridge = None
        mcp._usage_collector = None

        called_with: list[dict] = []

        import contextlib

        @contextlib.asynccontextmanager
        async def capturing_async_serve(*args, **kwargs):
            called_with.append({"args": args, "kwargs": kwargs})
            yield MagicMock()

        with patch("apcore_mcp.apcore_mcp.async_serve", capturing_async_serve):
            async with mcp.async_serve():
                pass

        assert len(called_with) == 1, (
            "APCoreMCP.async_serve() must delegate to module-level async_serve(). "
            f"Called {len(called_with)} times."
        )


# ---------------------------------------------------------------------------
# ISSUE 3/8 — D10-006: ErrorMapper.to_mcp_error snake_case top-level keys
# ---------------------------------------------------------------------------


class TestErrorMapperCamelCaseKeys:
    """D10-006 — to_mcp_error must emit camelCase top-level keys isError/errorType."""

    def _make_generic_error(self) -> Exception:
        """Unknown exception → goes through the generic path."""
        return RuntimeError("some error")

    def test_top_level_keys_are_camel_case(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        result = ErrorMapper().to_mcp_error(self._make_generic_error())
        assert "isError" in result, f"Expected 'isError' key, got keys: {list(result.keys())}"
        assert "errorType" in result, f"Expected 'errorType' key, got keys: {list(result.keys())}"

    def test_snake_case_keys_absent(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        result = ErrorMapper().to_mcp_error(self._make_generic_error())
        assert "is_error" not in result, "Snake-case 'is_error' must not be present"
        assert "error_type" not in result, "Snake-case 'error_type' must not be present"

    def test_is_error_value_is_true(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        result = ErrorMapper().to_mcp_error(self._make_generic_error())
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# ISSUE 4/8 — D9-004: APCORE_EVENTS dead export
# ---------------------------------------------------------------------------


class TestApCoreEventsRemoved:
    """D9-004 — APCORE_EVENTS must be removed from the public API."""

    def test_apcore_events_not_in_module_dir(self) -> None:
        import apcore_mcp

        assert "APCORE_EVENTS" not in dir(apcore_mcp), (
            "APCORE_EVENTS is an unused dead export and must be removed from apcore_mcp"
        )

    def test_apcore_events_not_importable(self) -> None:
        with pytest.raises((ImportError, AttributeError)):
            from apcore_mcp import APCORE_EVENTS  # type: ignore[attr-defined]  # noqa: F401


# ---------------------------------------------------------------------------
# ISSUE 5/8 — D9-005: MCP_DEFAULTS 9 keys never read by serve()
# ---------------------------------------------------------------------------


class TestMCPDefaultsWiredIntoServe:
    """D9-005 — Config Bus mcp.host value must be used when no host= kwarg is passed."""

    def test_serve_uses_config_bus_host_when_no_kwarg(self) -> None:
        """serve() must fall back to Config Bus mcp.host when host is not passed explicitly.

        We verify this by checking that the serve() function reads mcp.host from config
        and passes it to the transport manager when transport="streamable-http".
        """
        import apcore_mcp

        transport_manager_calls: list[dict] = []

        mock_config = MagicMock()
        mock_config.get = lambda key: {
            "mcp.host": "1.2.3.4",
            "mcp.port": 9999,
            "mcp.transport": "streamable-http",
            "mcp.pipeline": None,
            "mcp.middleware": None,
            "mcp.acl": None,
        }.get(key)

        mock_registry = MagicMock()
        mock_registry.list.return_value = []
        mock_registry.get_definition.return_value = None

        # MCPServerFactory and TransportManager are bound names in apcore_mcp.__init__
        # after the top-level imports, so patch them there.
        with (
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
        ):
            mock_factory = MagicMock()
            mock_factory_cls.return_value = mock_factory
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = MagicMock()
            mock_tm_cls.return_value = mock_tm

            # Capture arguments to run_streamable_http
            async def capturing_run_http(server, init_opts, *, host, port, **kwargs):
                transport_manager_calls.append({"host": host, "port": port})

            mock_tm.run_streamable_http = capturing_run_http

            # Patch Config.load() to return our mock config
            with patch("apcore.Config") as mock_config_cls:
                mock_config_cls.load.return_value = mock_config

                import asyncio
                import apcore_mcp as _am

                # Monkey-patch the resolve_registry/resolve_executor so we control what
                # the serve() function receives
                original_rr = _am.resolve_registry
                original_re = _am.resolve_executor
                try:
                    _am.resolve_registry = lambda x: mock_registry  # type: ignore[assignment]
                    _am.resolve_executor = lambda *a, **kw: MagicMock()  # type: ignore[assignment]

                    # serve() calls asyncio.run() — we intercept via the captured calls
                    try:
                        apcore_mcp.serve(
                            mock_registry,
                            transport="streamable-http",
                            # NOT passing host= — should come from Config Bus
                        )
                    except Exception:
                        pass  # asyncio.run may error in test context; we only care about call args
                finally:
                    _am.resolve_registry = original_rr
                    _am.resolve_executor = original_re

        # The call must have happened with the Config Bus host
        assert len(transport_manager_calls) == 1, (
            "serve() should have called run_streamable_http once. "
            f"Calls: {transport_manager_calls}"
        )
        assert transport_manager_calls[0]["host"] == "1.2.3.4", (
            f"serve() should have used mcp.host='1.2.3.4' from Config Bus, "
            f"got: {transport_manager_calls[0]['host']}"
        )


# ---------------------------------------------------------------------------
# ISSUE 6/8 — D9-006: apcore-toolkit declared runtime dep but never imported
# ---------------------------------------------------------------------------


class TestApcoreToolkitOptionalDep:
    """D9-006 — apcore-toolkit must not be a mandatory runtime dependency."""

    def test_apcore_toolkit_not_in_runtime_deps(self) -> None:
        """pyproject.toml must not list apcore-toolkit under [project.dependencies]."""
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject_path.read_text()

        # Parse the dependencies section — look for apcore-toolkit in the
        # [project.dependencies] section (before the first optional-deps section)
        lines = content.splitlines()
        in_deps = False
        in_optional = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[project.dependencies]":
                in_deps = True
                in_optional = False
            elif stripped.startswith("[project.optional-dependencies"):
                in_optional = True
                in_deps = False
            elif stripped.startswith("[") and in_deps:
                in_deps = False

            if in_deps and "apcore-toolkit" in stripped:
                pytest.fail(
                    "apcore-toolkit must NOT be in [project.dependencies]. "
                    "It is never imported at runtime — move it to [project.optional-dependencies]."
                )

    def test_apcore_toolkit_not_imported_at_module_level(self) -> None:
        """Importing apcore_mcp must not require apcore_toolkit."""
        import sys

        # Remove apcore_toolkit from sys.modules if present
        saved = sys.modules.pop("apcore_toolkit", None)
        try:
            # Simulate apcore_toolkit being absent
            sys.modules["apcore_toolkit"] = None  # type: ignore[assignment]
            # Re-importing apcore_mcp must not raise ImportError for apcore_toolkit
            import importlib

            import apcore_mcp
            importlib.reload(apcore_mcp)
        except ImportError as e:
            if "apcore_toolkit" in str(e):
                pytest.fail(f"apcore_mcp imports apcore_toolkit at module level: {e}")
        finally:
            if saved is not None:
                sys.modules["apcore_toolkit"] = saved
            else:
                sys.modules.pop("apcore_toolkit", None)


# ---------------------------------------------------------------------------
# ISSUE 7/8 — D9-010: MCPErrorFormatter is a 3-line passthrough wrapper
# ---------------------------------------------------------------------------


class TestErrorMapperHasFormatMethod:
    """D9-010 — ErrorMapper.format() must return the same result as to_mcp_error()."""

    def _sample_error(self) -> Exception:
        return RuntimeError("test error")

    def test_error_mapper_has_format_method(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        mapper = ErrorMapper()
        assert hasattr(mapper, "format"), "ErrorMapper must have a format() method"

    def test_format_returns_same_as_to_mcp_error(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        mapper = ErrorMapper()
        error = self._sample_error()
        result_format = mapper.format(error)
        result_to_mcp = mapper.to_mcp_error(error)
        assert result_format == result_to_mcp, (
            f"format() must return same result as to_mcp_error(). "
            f"format={result_format}, to_mcp_error={result_to_mcp}"
        )

    def test_format_accepts_context_kwarg(self) -> None:
        from apcore_mcp.adapters.errors import ErrorMapper

        mapper = ErrorMapper()
        # Must not raise
        result = mapper.format(self._sample_error(), context={"some": "context"})
        assert result is not None


# ---------------------------------------------------------------------------
# ISSUE 8/8 — D11-019: arguments stringification dict.repr vs JSON
# ---------------------------------------------------------------------------


class TestApprovalArgumentsJsonSerialization:
    """D11-019 — ElicitationApprovalHandler must use JSON for arguments formatting."""

    async def test_arguments_formatted_as_json_not_repr(self) -> None:
        """ApprovalRequest with dict arguments must produce JSON double-quote format."""
        from apcore_mcp.adapters.approval import ElicitationApprovalHandler
        from apcore_mcp.helpers import MCP_ELICIT_KEY

        handler = ElicitationApprovalHandler()

        # Capture the message passed to the elicit callback
        captured_messages: list[str] = []

        async def fake_elicit(message: str) -> dict:
            captured_messages.append(message)
            return {"action": "accept"}

        # Build a mock ApprovalRequest
        mock_context = MagicMock()
        mock_context.data = {MCP_ELICIT_KEY: fake_elicit}

        mock_request = MagicMock()
        mock_request.module_id = "test.module"
        mock_request.description = "Test description"
        mock_request.arguments = {"key": "val", "count": 42}
        mock_request.context = mock_context

        await handler.request_approval(mock_request)

        assert len(captured_messages) == 1
        message = captured_messages[0]

        # JSON format uses double quotes: {"key": "val"}
        assert '{"key": "val"' in message or '"key": "val"' in message, (
            f"Arguments must be JSON-serialized (double quotes). Got: {message!r}"
        )
        # Must NOT use Python repr format (single quotes)
        assert "{'key': 'val'" not in message, (
            f"Arguments must NOT use Python repr format. Got: {message!r}"
        )
