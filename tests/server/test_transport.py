"""Tests for TransportManager: MCP server transport lifecycle management."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from apcore_mcp.server.transport import TransportManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_server() -> MagicMock:
    """Create a mock MCP Server with an async run method."""
    server = MagicMock(spec=["run"])
    server.run = AsyncMock()
    return server


def make_mock_init_options() -> MagicMock:
    """Create a mock InitializationOptions."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Construction / initialization tests
# ---------------------------------------------------------------------------


class TestTransportManagerInstantiation:
    """Test that TransportManager can be created and has the expected API."""

    def test_transport_manager_instantiation(self) -> None:
        """TransportManager can be instantiated without arguments."""
        tm = TransportManager()
        assert tm is not None

    def test_run_stdio_is_async(self) -> None:
        """run_stdio is a coroutine function."""
        tm = TransportManager()
        assert inspect.iscoroutinefunction(tm.run_stdio)

    def test_run_streamable_http_is_async(self) -> None:
        """run_streamable_http is a coroutine function."""
        tm = TransportManager()
        assert inspect.iscoroutinefunction(tm.run_streamable_http)

    def test_run_sse_is_async(self) -> None:
        """run_sse is a coroutine function."""
        tm = TransportManager()
        assert inspect.iscoroutinefunction(tm.run_sse)


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidateHostPort:
    """Test _validate_host_port with various inputs."""

    def test_validate_host_port_valid(self) -> None:
        """Valid host and port raises no error."""
        tm = TransportManager()
        # Should not raise
        tm._validate_host_port("127.0.0.1", 8000)

    def test_validate_host_port_empty_host(self) -> None:
        """Empty host string raises ValueError."""
        tm = TransportManager()
        with pytest.raises(ValueError, match="[Hh]ost"):
            tm._validate_host_port("", 8000)

    def test_validate_host_port_zero(self) -> None:
        """Port 0 raises ValueError."""
        tm = TransportManager()
        with pytest.raises(ValueError, match="[Pp]ort"):
            tm._validate_host_port("127.0.0.1", 0)

    def test_validate_host_port_negative(self) -> None:
        """Negative port raises ValueError."""
        tm = TransportManager()
        with pytest.raises(ValueError, match="[Pp]ort"):
            tm._validate_host_port("127.0.0.1", -1)

    def test_validate_host_port_too_high(self) -> None:
        """Port > 65535 raises ValueError."""
        tm = TransportManager()
        with pytest.raises(ValueError, match="[Pp]ort"):
            tm._validate_host_port("127.0.0.1", 65536)

    def test_validate_host_port_boundary_low(self) -> None:
        """Port 1 is valid (lower boundary)."""
        tm = TransportManager()
        # Should not raise
        tm._validate_host_port("127.0.0.1", 1)

    def test_validate_host_port_boundary_high(self) -> None:
        """Port 65535 is valid (upper boundary)."""
        tm = TransportManager()
        # Should not raise
        tm._validate_host_port("127.0.0.1", 65535)


# ---------------------------------------------------------------------------
# Validation integration tests (validation runs before transport starts)
# ---------------------------------------------------------------------------


class TestBuildHealthResponse:
    """Tests for TransportManager._build_health_response."""

    def test_health_response_structure(self) -> None:
        """Health response contains required keys."""
        tm = TransportManager()
        response = tm._build_health_response()
        assert response["status"] == "ok"
        assert "uptime_seconds" in response
        assert response["module_count"] == 0

    def test_health_response_with_module_count(self) -> None:
        """Health response includes stored module count via set_module_count."""
        tm = TransportManager()
        tm.set_module_count(5)
        response = tm._build_health_response()
        assert response["module_count"] == 5

    def test_health_response_uptime_increases(self) -> None:
        """Uptime should be non-negative."""
        tm = TransportManager()
        response = tm._build_health_response()
        assert response["uptime_seconds"] >= 0

    def test_set_module_count(self) -> None:
        """set_module_count updates the stored module count."""
        tm = TransportManager()
        assert tm._build_health_response()["module_count"] == 0
        tm.set_module_count(10)
        assert tm._build_health_response()["module_count"] == 10


class TestTransportValidationIntegration:
    """Verify that run_streamable_http and run_sse validate before starting."""

    async def test_run_streamable_http_validates_port(self) -> None:
        """run_streamable_http raises ValueError for invalid port before starting server."""
        tm = TransportManager()
        server = make_mock_server()
        init_options = make_mock_init_options()

        with pytest.raises(ValueError, match="[Pp]ort"):
            await tm.run_streamable_http(server, init_options, host="127.0.0.1", port=0)

        # Server.run should never have been called
        server.run.assert_not_called()

    async def test_run_sse_validates_host(self) -> None:
        """run_sse raises ValueError for empty host before starting server."""
        tm = TransportManager()
        server = make_mock_server()
        init_options = make_mock_init_options()

        with pytest.raises(ValueError, match="[Hh]ost"):
            await tm.run_sse(server, init_options, host="", port=8000)

        # Server.run should never have been called
        server.run.assert_not_called()

    async def test_run_streamable_http_validates_host(self) -> None:
        """run_streamable_http raises ValueError for empty host."""
        tm = TransportManager()
        server = make_mock_server()
        init_options = make_mock_init_options()

        with pytest.raises(ValueError, match="[Hh]ost"):
            await tm.run_streamable_http(server, init_options, host="", port=8000)

        server.run.assert_not_called()

    async def test_run_sse_validates_port(self) -> None:
        """run_sse raises ValueError for port > 65535."""
        tm = TransportManager()
        server = make_mock_server()
        init_options = make_mock_init_options()

        with pytest.raises(ValueError, match="[Pp]ort"):
            await tm.run_sse(server, init_options, host="127.0.0.1", port=70000)

        server.run.assert_not_called()


# ---------------------------------------------------------------------------
# TM-4: AsyncTaskBridge cancellation forwarding on disconnect
# ---------------------------------------------------------------------------


class TestTM4CancellationForwarding:
    """The transport must call ``bridge.cancel_session_tasks(session_id)``
    when the transport closes — mirrors TS ``setAsyncTaskBridge`` and Rust
    ``set_cancel_handler``. Tasks submitted while the contextvar is set get
    keyed under that session and cancelled together on disconnect.
    """

    def test_set_async_task_bridge_stores_bridge(self) -> None:
        """set_async_task_bridge accepts any object with cancel_session_tasks."""
        tm = TransportManager()
        bridge = MagicMock()
        bridge.cancel_session_tasks = AsyncMock(return_value=0)
        tm.set_async_task_bridge(bridge)
        assert tm._async_task_bridge is bridge

    @pytest.mark.asyncio
    async def test_scoped_session_sets_and_resets_contextvar(self) -> None:
        """The contextvar holds the active session id only inside the block."""
        from apcore_mcp.server.transport import transport_session_var

        tm = TransportManager()
        assert transport_session_var.get() is None

        async with tm._scoped_session("sess-abc"):
            assert transport_session_var.get() == "sess-abc"

        assert transport_session_var.get() is None

    @pytest.mark.asyncio
    async def test_scoped_session_calls_cancel_on_exit(self) -> None:
        """Bridge.cancel_session_tasks must be invoked once on context exit."""
        tm = TransportManager()
        bridge = MagicMock()
        bridge.cancel_session_tasks = AsyncMock(return_value=2)
        tm.set_async_task_bridge(bridge)

        async with tm._scoped_session("sess-xyz"):
            pass

        bridge.cancel_session_tasks.assert_awaited_once_with("sess-xyz")

    @pytest.mark.asyncio
    async def test_scoped_session_swallows_bridge_errors(self) -> None:
        """Disconnect path must not raise even if cancel_session_tasks fails."""
        tm = TransportManager()
        bridge = MagicMock()
        bridge.cancel_session_tasks = AsyncMock(side_effect=RuntimeError("boom"))
        tm.set_async_task_bridge(bridge)

        # Must not raise
        async with tm._scoped_session("sess-err"):
            pass

        bridge.cancel_session_tasks.assert_awaited_once_with("sess-err")

    @pytest.mark.asyncio
    async def test_no_bridge_no_cancel_call(self) -> None:
        """When no bridge is set, the scoped session is a no-op on exit."""
        tm = TransportManager()
        async with tm._scoped_session("sess-none"):
            pass
        # No exception, nothing to assert beyond reaching this line.

    @pytest.mark.asyncio
    async def test_session_key_visible_during_scope(self) -> None:
        """A nested call (simulating factory.handle_call_tool) must see the id."""
        from apcore_mcp.server.transport import transport_session_var

        tm = TransportManager()
        captured: list[str | None] = []

        async def inner() -> None:
            captured.append(transport_session_var.get())

        async with tm._scoped_session("sess-inner"):
            await inner()

        assert captured == ["sess-inner"]
