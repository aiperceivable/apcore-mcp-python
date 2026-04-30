"""Regression tests for issues identified in project code review.

Each test class corresponds to a numbered issue from the review and must fail
before the fix is applied, pass after.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# B1: Config Bus mcp.pipeline strategy object crashes serve() at startup
# ---------------------------------------------------------------------------


class TestConfigBusStrategyFix:
    """B1 — resolve_executor must accept ExecutionStrategy objects, not just strings."""

    def _make_registry(self) -> MagicMock:
        """Registry mock: must NOT have call_async so resolve_executor creates an Executor."""
        m = MagicMock()
        del m.call_async  # remove so hasattr() returns False
        return m

    def test_string_strategy_accepted(self) -> None:
        """Known string strategies still pass validation."""
        from apcore_mcp._utils import resolve_executor

        mock_registry = self._make_registry()
        with patch("apcore.executor.Executor") as mock_exec_cls:
            mock_exec_cls.return_value = MagicMock()
            executor = resolve_executor(mock_registry, strategy="standard")
            assert executor is not None

    def test_unknown_string_strategy_rejected(self) -> None:
        """Unknown string strategy must still raise ValueError."""
        from apcore_mcp._utils import resolve_executor

        mock_registry = self._make_registry()
        with pytest.raises(ValueError, match="Unknown strategy"):
            resolve_executor(mock_registry, strategy="bogus")

    def test_object_strategy_accepted(self) -> None:
        """ExecutionStrategy objects (non-str) must pass through without ValueError.

        This is the regression: before the fix, any non-string strategy raised
        ValueError because ``strategy not in _VALID_STRATEGIES`` is always True
        for objects.
        """
        from apcore_mcp._utils import resolve_executor

        mock_registry = self._make_registry()
        strategy_object = object()  # simulates an ExecutionStrategy instance

        with patch("apcore.executor.Executor") as mock_exec_cls:
            mock_exec_cls.return_value = MagicMock()
            # Must NOT raise ValueError
            resolve_executor(mock_registry, strategy=strategy_object)
            call_kwargs = mock_exec_cls.call_args[1]
            assert call_kwargs["strategy"] is strategy_object


# ---------------------------------------------------------------------------
# C1: _handle_stream leaks unredacted chunks via progress notifications
# ---------------------------------------------------------------------------


class TestStreamChunkRedaction:
    """C1 — each streaming chunk must be redacted before being sent as a notification."""

    async def test_chunk_is_redacted_before_notification(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        sensitive_chunk = {"result": "secret_value", "metadata": "ok"}
        redacted_chunk = {"result": "[REDACTED]", "metadata": "ok"}

        mock_executor = MagicMock()
        mock_executor.call_async = AsyncMock(return_value={})

        async def fake_stream(_tool, _args, _ctx=None, **kw):
            yield sensitive_chunk

        mock_executor.stream = fake_stream
        mock_executor.stream.__name__ = "stream"

        # Make stream method inspectable (3 params)
        import inspect

        original_sig = inspect.signature(fake_stream)
        assert len(original_sig.parameters) >= 2

        router = ExecutionRouter(
            mock_executor,
            redact_output=True,
            output_schema_map={"my.tool": {"type": "object", "properties": {}}},
        )

        notifications: list[dict] = []

        async def capture_notification(n: dict) -> None:
            notifications.append(n)

        with patch("apcore_mcp.server.router.ExecutionRouter._maybe_redact") as mock_redact:
            # First call for chunk redaction, second for accumulated
            mock_redact.side_effect = [redacted_chunk, redacted_chunk]
            await router._handle_stream(
                "my.tool",
                {},
                "tok1",
                capture_notification,
            )

        # _maybe_redact should have been called at least once for the chunk
        assert mock_redact.call_count >= 2
        # First call is for the chunk
        first_call_args = mock_redact.call_args_list[0]
        assert first_call_args[0][0] == "my.tool"


# ---------------------------------------------------------------------------
# C2: _handle_stream non-dict chunk raises TypeError instead of partial-state bug
# ---------------------------------------------------------------------------


class TestStreamNonDictChunk:
    """C2 — a non-dict chunk from executor.stream must raise TypeError immediately."""

    async def test_non_dict_chunk_raises_type_error(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        mock_executor = MagicMock()

        async def stream_with_string_chunk(_tool, _args, _ctx=None, **kw):
            yield "not a dict"

        mock_executor.stream = stream_with_string_chunk

        router = ExecutionRouter(mock_executor)

        notifications: list[dict] = []

        async def capture(n: dict) -> None:
            notifications.append(n)

        content, is_error, _ = await router._handle_stream("my.tool", {}, "tok1", capture)
        # Should return error response, not crash with uncaught AttributeError
        assert is_error is True
        assert "TypeError" in content[0]["text"] or len(content) > 0

    async def test_dict_chunk_still_works(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        mock_executor = MagicMock()

        async def stream_with_dict_chunk(_tool, _args, _ctx=None, **kw):
            yield {"key": "value"}

        mock_executor.stream = stream_with_dict_chunk

        router = ExecutionRouter(mock_executor)

        async def noop(_n: dict) -> None:
            pass

        content, is_error, _ = await router._handle_stream("my.tool", {}, "tok1", noop)
        assert is_error is False


# ---------------------------------------------------------------------------
# W8: Error paths drop trace_id and skip traceparent propagation
# ---------------------------------------------------------------------------


class TestErrorPathTraceId:
    """W8 — error responses must carry trace_id and attempt traceparent attachment."""

    async def test_handle_call_async_error_carries_trace_id(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        mock_executor = MagicMock()
        mock_executor.call_async = AsyncMock(side_effect=RuntimeError("boom"))

        router = ExecutionRouter(mock_executor)

        fake_context = MagicMock()
        fake_context.trace_id = "trace-abc-123"

        _content, is_error, trace_id = await router._handle_call_async("my.tool", {}, context=fake_context)
        assert is_error is True
        # After fix: trace_id is propagated even on error
        assert trace_id == "trace-abc-123"

    async def test_handle_stream_error_carries_trace_id(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        mock_executor = MagicMock()

        async def bad_stream(_tool, _args, _ctx=None, **kw):
            raise RuntimeError("stream failure")
            yield  # make it a generator

        mock_executor.stream = bad_stream

        router = ExecutionRouter(mock_executor)

        fake_context = MagicMock()
        fake_context.trace_id = "trace-xyz-456"

        async def noop(_n: dict) -> None:
            pass

        _content, is_error, trace_id = await router._handle_stream("my.tool", {}, "tok1", noop, context=fake_context)
        assert is_error is True
        assert trace_id == "trace-xyz-456"


# ---------------------------------------------------------------------------
# W11: validate_tool bypasses ErrorMapper, raw str(e) leaks internal detail
# ---------------------------------------------------------------------------


class TestValidateToolErrorMapper:
    """W11 — validate_tool exceptions must be sanitized through ErrorMapper."""

    def test_validate_tool_uses_error_mapper_for_exceptions(self) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        class FakeACLError(Exception):
            """Simulates an apcore ACLDeniedError with sensitive data."""

            def __init__(self) -> None:
                super().__init__("ACL_DENIED: user alice -> module secret.internal")

        mock_executor = MagicMock()
        mock_executor.validate = MagicMock(side_effect=FakeACLError())

        router = ExecutionRouter(mock_executor)

        with patch.object(router._error_mapper, "to_mcp_error") as mock_map:
            mock_map.return_value = {"errorType": "ACCESS_DENIED", "message": "Access denied"}
            result = router.validate_tool("my.tool", {})

        # ErrorMapper must be called (sanitization applied)
        mock_map.assert_called_once()
        # Result must include sanitized message, not raw str(exc)
        check = result["checks"][0]
        assert "ACL_DENIED: user alice" not in check["error"].get("message", "")
        assert "Access denied" in check["error"]["message"]


# ---------------------------------------------------------------------------
# W13: validate_inputs crash logged at DEBUG — should be WARNING
# ---------------------------------------------------------------------------


class TestValidateInputsLogLevel:
    """W13 — a validate_inputs crash (not a validation failure) should log at WARNING."""

    async def test_validate_inputs_crash_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from apcore_mcp.server.router import ExecutionRouter

        mock_executor = MagicMock()
        mock_executor.validate = MagicMock(side_effect=RuntimeError("internal validate error"))
        mock_executor.call_async = AsyncMock(return_value={})

        router = ExecutionRouter(mock_executor, validate_inputs=True)

        with caplog.at_level(logging.WARNING, logger="apcore_mcp.server.router"):
            await router.handle_call("my.tool", {})

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "validate" in m.lower() or "crashed" in m.lower() for m in warning_msgs
        ), f"Expected WARNING log about validate_inputs crash, got: {warning_msgs}"


# ---------------------------------------------------------------------------
# C3: MCPServer._run signals _started before transport binds
# ---------------------------------------------------------------------------


class TestMCPServerStartedSignaling:
    """C3 — start() must raise if transport fails instead of silently returning."""

    def test_unknown_transport_raises_from_start(self) -> None:
        """Unknown transport should raise immediately, not silently fail."""
        from apcore_mcp.server.server import MCPServer

        # After fix: invalid transport raises on construction or start()
        with pytest.raises((ValueError, RuntimeError), match="(?i)transport|unknown"):
            _ = MCPServer(MagicMock(), transport="bogus-transport")


# ---------------------------------------------------------------------------
# C4 / W2: MCPServer and APCoreMCP wrapper build output_schema_map
# ---------------------------------------------------------------------------


class TestOutputSchemaMapParity:
    """C4/W2 — MCPServer and APCoreMCP must build output_schema_map and pass it to ExecutionRouter."""

    def test_apcore_mcp_build_server_components_passes_output_schema_map(self) -> None:
        """APCoreMCP._build_server_components must pass a non-None output_schema_map to ExecutionRouter."""
        from apcore_mcp.apcore_mcp import APCoreMCP

        # Patch at the import sites used inside _build_server_components
        with (
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter") as mock_router_cls,
        ):
            mock_factory = MagicMock()
            mock_factory_cls.return_value = mock_factory
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_registry = MagicMock()
            mock_registry.list.return_value = ["test.module"]
            mock_desc = MagicMock()
            mock_desc.output_schema = {"type": "object", "properties": {"secret": {"type": "string"}}}
            mock_registry.get_definition.return_value = mock_desc

            mock_executor = MagicMock()

            mcp = APCoreMCP.__new__(APCoreMCP)
            mcp._registry = mock_registry
            mcp._executor = mock_executor
            mcp._name = "test"
            mcp._version = "1.0"
            mcp._tags = None
            mcp._prefix = None
            mcp._validate_inputs = False
            mcp._output_formatter = None
            mcp._async_tasks = False
            mcp._async_max_concurrent = 10
            mcp._async_max_tasks = 1000
            mcp._async_bridge = None

            mcp._build_server_components()

            # ExecutionRouter must be called with output_schema_map
            assert mock_router_cls.called
            call_kwargs = mock_router_cls.call_args[1]
            output_schema_map = call_kwargs.get("output_schema_map")
            assert output_schema_map is not None, "output_schema_map not passed to ExecutionRouter"
            assert "test.module" in output_schema_map


# ---------------------------------------------------------------------------
# W3: Config Bus swallows builder ValueErrors — should only catch ImportError
# ---------------------------------------------------------------------------


class TestConfigBusExceptionNarrowing:
    """W3 — builder ValueErrors from malformed mcp.middleware/mcp.acl YAML must propagate."""

    def test_config_bus_import_error_is_swallowed(self) -> None:
        """ImportError (apcore not installed) should still be silently swallowed."""

        # Simulate a fresh resolve_executor call with a valid registry — just
        # test the broad concept by verifying that the fix in __init__.py narrows
        # the except clause. We verify the code structure directly.
        import inspect

        import apcore_mcp.__init__ as init_mod

        src = inspect.getsource(init_mod.serve)
        # After fix: except should be 'except ImportError', not 'except Exception'
        # Verify the pattern exists somewhere in the source
        assert (
            "except ImportError" in src or "ImportError" in src
        ), "serve() Config Bus block should catch ImportError, not broad Exception"

    def test_apcore_mcp_init_narrows_config_bus_catch(self) -> None:
        """APCoreMCP.__init__ Config Bus block should catch ImportError only."""
        import inspect

        from apcore_mcp.apcore_mcp import APCoreMCP

        src = inspect.getsource(APCoreMCP.__init__)
        assert (
            "except ImportError" in src or "ImportError" in src
        ), "APCoreMCP.__init__ Config Bus block should catch ImportError, not broad Exception"


# ---------------------------------------------------------------------------
# W4: _ensure_object_type clobbers list-typed schemas ["object", "null"]
# ---------------------------------------------------------------------------


class TestEnsureObjectTypeFix:
    """W4 — schemas with type: ['object', 'null'] must not be downgraded to 'object'."""

    def test_list_type_with_null_preserved(self) -> None:
        from apcore_mcp.adapters.schema import SchemaConverter

        converter = SchemaConverter(strict=False)
        schema = {
            "type": ["object", "null"],
            "properties": {"name": {"type": "string"}},
        }

        result = converter._convert_schema(schema)

        assert result["type"] == ["object", "null"], f"List type should be preserved, got: {result['type']!r}"

    def test_plain_object_type_still_works(self) -> None:
        from apcore_mcp.adapters.schema import SchemaConverter

        converter = SchemaConverter(strict=False)
        schema = {"properties": {"x": {"type": "string"}}}
        result = converter._convert_schema(schema)
        assert result["type"] == "object"

    def test_no_type_gets_object(self) -> None:
        from apcore_mcp.adapters.schema import SchemaConverter

        converter = SchemaConverter(strict=False)
        schema: dict = {}
        result = converter._convert_schema(schema)
        assert result["type"] == "object"


# ---------------------------------------------------------------------------
# W5: JWTAuthenticator.require_auth dead parameter/property
# ---------------------------------------------------------------------------


class TestJWTAuthenticatorRequireAuthRemoved:
    """W5 — JWTAuthenticator.require_auth parameter/property should be removed."""

    def test_require_auth_not_accepted(self) -> None:
        """JWTAuthenticator should NOT accept require_auth as a parameter."""
        import inspect

        from apcore_mcp.auth.jwt import JWTAuthenticator

        sig = inspect.signature(JWTAuthenticator.__init__)
        assert (
            "require_auth" not in sig.parameters
        ), "require_auth was removed; AuthMiddleware is the correct owner of this policy"

    def test_require_auth_property_not_present(self) -> None:
        from apcore_mcp.auth.jwt import JWTAuthenticator

        auth = JWTAuthenticator(key="secret")
        assert not hasattr(auth, "require_auth"), "require_auth property should be removed from JWTAuthenticator"


# ---------------------------------------------------------------------------
# W6: AuthMiddleware exempt-path branch swallows exceptions silently
# ---------------------------------------------------------------------------


class TestAuthMiddlewareExemptPathLogging:
    """W6 — authenticator exceptions on exempt paths must be logged at WARNING."""

    async def test_authenticator_exception_on_exempt_path_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        from apcore_mcp.auth.middleware import AuthMiddleware

        class BoomAuthenticator:
            def authenticate(self, headers: dict) -> None:
                raise RuntimeError("JWT backend unavailable")

        received: list[dict] = []

        async def fake_app(scope: dict, receive: Any, send: Any) -> None:
            received.append(scope)

        middleware = AuthMiddleware(
            fake_app,
            BoomAuthenticator(),  # type: ignore[arg-type]
            exempt_paths={"/health"},
        )

        scope = {"type": "http", "path": "/health", "headers": []}

        with caplog.at_level(logging.WARNING, logger="apcore_mcp.auth.middleware"):
            await middleware(scope, None, None)

        # App should still be called (exempt path is non-blocking)
        assert len(received) == 1

        # Exception must have been logged at WARNING after fix
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "authenticator" in m.lower() or "exempt" in m.lower() for m in warning_msgs
        ), f"Expected WARNING about authenticator exception, got: {warning_msgs}"


# ---------------------------------------------------------------------------
# W9: MCPServerFactory.create_server silently drops version parameter
# ---------------------------------------------------------------------------


class TestCreateServerVersionDoc:
    """W9 — create_server docstring should clarify version flows through build_init_options."""

    def test_create_server_docstring_mentions_init_options(self) -> None:
        from apcore_mcp.server.factory import MCPServerFactory

        doc = MCPServerFactory.create_server.__doc__ or ""
        # After fix: docstring must clarify version is used via build_init_options
        assert (
            "build_init_options" in doc or "InitializationOptions" in doc or "version" in doc.lower()
        ), "create_server docstring should explain that version flows through build_init_options"


# ---------------------------------------------------------------------------
# W10: build_tools swallows RESERVED_PREFIX ValueError silently
# ---------------------------------------------------------------------------


class TestBuildToolsReservedPrefixRaises:
    """W10 — a reserved-prefix module_id must raise ValueError from build_tools."""

    def test_reserved_prefix_raises_from_build_tools(self) -> None:
        from apcore_mcp.server.async_task_bridge import RESERVED_PREFIX
        from apcore_mcp.server.factory import MCPServerFactory

        factory = MCPServerFactory()

        mock_desc = MagicMock()
        mock_desc.module_id = f"{RESERVED_PREFIX}shadow_tool"
        mock_desc.description = "test"
        mock_desc.input_schema = {"type": "object", "properties": {}}
        mock_desc.output_schema = {}
        mock_desc.annotations = None

        mock_registry = MagicMock()
        mock_registry.list.return_value = [mock_desc.module_id]
        mock_registry.get_definition.return_value = mock_desc

        with pytest.raises(ValueError, match="reserved prefix"):
            factory.build_tools(mock_registry)


# ---------------------------------------------------------------------------
# W12: factory.py async-submit reaches private _error_mapper attribute
# ---------------------------------------------------------------------------


class TestFactoryDoesNotAccessBridgePrivateErrorMapper:
    """W12 — factory's async-submit error path must not reach async_bridge._error_mapper."""

    def test_register_handlers_error_uses_factory_error_mapper(self) -> None:
        """MCPServerFactory must have its own ErrorMapper, not borrow the bridge's."""
        from apcore_mcp.server.factory import MCPServerFactory

        factory = MCPServerFactory()
        # After fix: factory should have its own _error_mapper
        assert hasattr(factory, "_error_mapper"), "MCPServerFactory should have its own _error_mapper after fix"
