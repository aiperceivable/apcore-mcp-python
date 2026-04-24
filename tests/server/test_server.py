"""Unit tests for MCPServer non-blocking wrapper."""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apcore_mcp.server.server import MCPServer

# ---------------------------------------------------------------------------
# Stub Registry / Executor
# ---------------------------------------------------------------------------


class StubRegistry:
    """Minimal Registry stub."""

    def __init__(self) -> None:
        self._modules: dict[str, Any] = {}

    def list(self, tags: list[str] | None = None, prefix: str | None = None) -> list[str]:
        return list(self._modules.keys())

    def get_definition(self, module_id: str) -> Any:
        return self._modules.get(module_id)


class StubExecutor:
    """Minimal Executor stub with registry attribute."""

    def __init__(self) -> None:
        self.registry = StubRegistry()

    async def call_async(self, module_id: str, inputs: dict[str, Any]) -> Any:
        return {"ok": True}


# ---------------------------------------------------------------------------
# Tests for MCPServer.__init__ and properties
# ---------------------------------------------------------------------------


class TestMCPServerInit:
    """Tests for MCPServer constructor and properties."""

    def test_default_parameters(self) -> None:
        """MCPServer stores default parameters correctly."""
        registry = StubRegistry()
        server = MCPServer(registry)
        assert server._transport == "stdio"
        assert server._host == "127.0.0.1"
        assert server._port == 8000
        assert server._name == "apcore-mcp"
        assert server._version is None
        assert server._thread is None
        assert server._loop is None

    def test_custom_parameters(self) -> None:
        """MCPServer stores custom parameters correctly."""
        registry = StubRegistry()
        server = MCPServer(
            registry,
            transport="streamable-http",
            host="0.0.0.0",
            port=9000,
            name="custom-server",
            version="1.0.0",
        )
        assert server._transport == "streamable-http"
        assert server._host == "0.0.0.0"
        assert server._port == 9000
        assert server._name == "custom-server"
        assert server._version == "1.0.0"

    def test_started_and_stopped_events_initialized(self) -> None:
        """Internal threading events are properly initialized."""
        server = MCPServer(StubRegistry())
        assert isinstance(server._started, threading.Event)
        assert isinstance(server._stopped, threading.Event)
        assert not server._started.is_set()
        assert not server._stopped.is_set()


# ---------------------------------------------------------------------------
# Tests for MCPServer.address
# ---------------------------------------------------------------------------


class TestMCPServerAddress:
    """Tests for MCPServer.address property."""

    def test_stdio_address(self) -> None:
        """stdio transport returns 'stdio' as address."""
        server = MCPServer(StubRegistry(), transport="stdio")
        assert server.address == "stdio"

    def test_http_address(self) -> None:
        """HTTP transport returns formatted URL."""
        server = MCPServer(
            StubRegistry(),
            transport="streamable-http",
            host="0.0.0.0",
            port=9000,
        )
        assert server.address == "http://0.0.0.0:9000"

    def test_sse_address(self) -> None:
        """SSE transport returns formatted URL."""
        server = MCPServer(
            StubRegistry(),
            transport="sse",
            host="127.0.0.1",
            port=8080,
        )
        assert server.address == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# Tests for MCPServer.start / stop / wait
# ---------------------------------------------------------------------------


class TestMCPServerLifecycle:
    """Tests for MCPServer start/stop/wait lifecycle."""

    def test_start_creates_daemon_thread(self) -> None:
        """start() creates a daemon thread and waits for _started."""
        registry = StubRegistry()
        server = MCPServer(registry)

        # Mock _run to just set _started and block until _stopped
        def mock_run() -> None:
            server._started.set()
            server._stopped.wait()

        with patch.object(server, "_run", side_effect=mock_run):
            server.start()
            assert server._thread is not None
            assert server._thread.daemon is True
            assert server._started.is_set()
            server.stop()
            server._thread.join(timeout=2)

    def test_start_is_idempotent(self) -> None:
        """Calling start() twice does not create a second thread."""
        registry = StubRegistry()
        server = MCPServer(registry)

        def mock_run() -> None:
            server._started.set()
            server._stopped.wait()

        with patch.object(server, "_run", side_effect=mock_run):
            server.start()
            first_thread = server._thread
            server.start()  # Second call should be no-op
            assert server._thread is first_thread
            server.stop()
            server._thread.join(timeout=2)

    def test_wait_blocks_until_thread_finishes(self) -> None:
        """wait() blocks until the thread completes."""
        registry = StubRegistry()
        server = MCPServer(registry)

        def mock_run() -> None:
            server._started.set()
            # Immediately finish

        with patch.object(server, "_run", side_effect=mock_run):
            server.start()
            server.wait()  # Should return immediately since _run finishes
            assert not server._thread.is_alive()

    def test_wait_noop_without_start(self) -> None:
        """wait() does nothing if start() was never called."""
        server = MCPServer(StubRegistry())
        server.wait()  # Should not raise

    def test_stop_sets_stopped_event(self) -> None:
        """stop() sets the _stopped event."""
        server = MCPServer(StubRegistry())
        assert not server._stopped.is_set()
        server.stop()
        assert server._stopped.is_set()

    def test_stop_calls_loop_stop(self) -> None:
        """stop() calls loop.stop() when loop is available."""
        server = MCPServer(StubRegistry())
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        server._loop = mock_loop
        server.stop()
        mock_loop.call_soon_threadsafe.assert_called_once_with(mock_loop.stop)

    def test_stop_without_loop(self) -> None:
        """stop() handles None loop gracefully."""
        server = MCPServer(StubRegistry())
        server._loop = None
        server.stop()  # Should not raise
        assert server._stopped.is_set()


# ---------------------------------------------------------------------------
# Tests for MCPServer._run
# ---------------------------------------------------------------------------


class TestMCPServerRun:
    """Tests for MCPServer._run internal method."""

    def test_run_stdio(self) -> None:
        """_run with stdio transport calls run_stdio on TransportManager."""
        registry = StubRegistry()
        server = MCPServer(registry, transport="stdio")

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_executor = MagicMock()
            mock_resolve_exec.return_value = mock_executor
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_mcp_server = MagicMock()
            mock_factory.create_server.return_value = mock_mcp_server
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            # Make run_stdio a coroutine that completes immediately
            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            assert server._loop is not None
            assert server._started.is_set()
            assert server._stopped.is_set()

    def test_run_streamable_http(self) -> None:
        """_run with streamable-http transport calls run_streamable_http."""
        registry = StubRegistry()
        server = MCPServer(
            registry,
            transport="streamable-http",
            host="0.0.0.0",
            port=9000,
        )

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_resolve_exec.return_value = MagicMock()
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_streamable_http = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            assert server._stopped.is_set()

    def test_run_sse(self) -> None:
        """_run with sse transport calls run_sse."""
        registry = StubRegistry()
        server = MCPServer(registry, transport="sse", host="127.0.0.1", port=8080)

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_resolve_exec.return_value = MagicMock()
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_sse = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            assert server._stopped.is_set()

    def test_run_unknown_transport_raises(self) -> None:
        """Unknown transport raises ValueError at construction time."""
        registry = StubRegistry()
        # Transport is now validated in __init__ so the error surfaces immediately.
        with pytest.raises(ValueError, match="Unknown transport"):
            MCPServer(registry, transport="unknown")

    def test_run_uses_package_version_when_version_is_none(self) -> None:
        """_run uses __version__ from package when version is not specified."""
        from apcore_mcp import __version__

        registry = StubRegistry()
        server = MCPServer(registry, version=None)

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
            patch("importlib.metadata.version", return_value=__version__),
        ):
            mock_resolve_exec.return_value = MagicMock()
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            # Verify build_init_options was called with package version
            call_kwargs = mock_factory.build_init_options.call_args
            from apcore_mcp import __version__

            assert call_kwargs.kwargs["version"] == __version__ or call_kwargs[1]["version"] == __version__

    def test_run_with_executor(self) -> None:
        """_run works with an Executor (not just Registry)."""
        executor = StubExecutor()
        server = MCPServer(executor, transport="stdio")

        with (
            patch("apcore_mcp._utils.resolve_registry") as mock_resolve_reg,
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_resolve_reg.return_value = executor.registry
            mock_resolve_exec.return_value = executor
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            mock_resolve_reg.assert_called_once_with(executor)
            mock_resolve_exec.assert_called_once_with(executor)

    def test_run_forwards_validate_inputs(self) -> None:
        """_run passes validate_inputs to ExecutionRouter."""
        registry = StubRegistry()
        server = MCPServer(registry, transport="stdio", validate_inputs=True)

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter") as mock_router_cls,
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_executor = MagicMock()
            mock_resolve_exec.return_value = mock_executor
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def noop(*args: Any, **kwargs: Any) -> None:
                pass

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: noop())

            server._run()

            mock_router_cls.assert_called_once_with(
                mock_executor,
                validate_inputs=True,
                output_schema_map={},
            )

    def test_run_closes_loop_on_error(self) -> None:
        """_run closes the event loop even if transport raises."""
        registry = StubRegistry()
        server = MCPServer(registry, transport="stdio")

        with (
            patch("apcore_mcp._utils.resolve_registry", return_value=registry),
            patch("apcore_mcp._utils.resolve_executor") as mock_resolve_exec,
            patch("apcore_mcp.server.factory.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.server.router.ExecutionRouter"),
            patch("apcore_mcp.server.transport.TransportManager") as mock_transport_cls,
        ):
            mock_resolve_exec.return_value = MagicMock()
            mock_tm = mock_transport_cls.return_value
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            async def failing(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("Transport failed")

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: failing())

            # _run() no longer propagates — it stores the error and signals _started.
            server._run()

            # Error must be captured, not swallowed
            assert server._start_error is not None
            assert "Transport failed" in str(server._start_error)
            # Loop should be closed and stopped should be set despite error
            assert server._stopped.is_set()
