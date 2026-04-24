"""Non-blocking MCP server wrapper for framework integrations."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apcore_mcp.auth.protocol import Authenticator
    from apcore_mcp.server.transport import MetricsExporter

logger = logging.getLogger(__name__)


_VALID_TRANSPORTS = frozenset({"stdio", "streamable-http", "sse"})


class MCPServer:
    """Non-blocking MCP server.

    Usage:
        server = MCPServer(registry, transport="streamable-http", port=8000)
        server.start()
        print(f"Server running at {server.address}")
        server.wait()  # blocks until shutdown
    """

    def __init__(
        self,
        registry_or_executor: object,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        name: str = "apcore-mcp",
        version: str | None = None,
        validate_inputs: bool = False,
        metrics_collector: MetricsExporter | None = None,
        tags: list[str] | None = None,
        prefix: str | None = None,
        authenticator: Authenticator | None = None,
        require_auth: bool = True,
        exempt_paths: set[str] | None = None,
        async_tasks: bool = True,
        async_max_concurrent: int = 10,
        async_max_tasks: int = 1000,
    ) -> None:
        transport_lower = transport.lower()
        if transport_lower not in _VALID_TRANSPORTS:
            raise ValueError(
                f"Unknown transport: {transport!r}. Expected one of {sorted(_VALID_TRANSPORTS)}"
            )
        self._registry_or_executor = registry_or_executor
        self._transport = transport_lower
        self._host = host
        self._port = port
        self._name = name
        self._version = version
        self._validate_inputs = validate_inputs
        self._metrics_collector = metrics_collector
        self._tags = tags
        self._prefix = prefix
        self._authenticator = authenticator
        self._require_auth = require_auth
        self._exempt_paths = exempt_paths
        self._async_tasks = async_tasks
        self._async_max_concurrent = async_max_concurrent
        self._async_max_tasks = async_max_tasks
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._start_error: BaseException | None = None

    @property
    def address(self) -> str:
        """Server address (available after start)."""
        if self._transport == "stdio":
            return "stdio"
        return f"http://{self._host}:{self._port}"

    def start(self) -> None:
        """Start the server in a background thread (non-blocking).

        Raises:
            RuntimeError: if the server fails to initialise (e.g., port already in use).
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)
        if self._start_error is not None:
            err = self._start_error
            raise RuntimeError(f"MCP server failed to start: {err}") from err

    def wait(self) -> None:
        """Block until the server stops."""
        if self._thread is not None:
            self._thread.join()

    def stop(self) -> None:
        """Gracefully stop the server."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._stopped.set()

    def _run(self) -> None:
        """Internal: run the server event loop."""
        try:
            from importlib.metadata import PackageNotFoundError
            from importlib.metadata import version as _pkg_version

            from apcore_mcp._utils import resolve_executor, resolve_registry

            try:
                __version__ = _pkg_version("apcore-mcp")
            except PackageNotFoundError:
                __version__ = "unknown"
            from apcore_mcp.server.factory import MCPServerFactory
            from apcore_mcp.server.router import ExecutionRouter
            from apcore_mcp.server.transport import TransportManager

            registry = resolve_registry(self._registry_or_executor)
            executor = resolve_executor(self._registry_or_executor)
            version = self._version or __version__

            # Build output schema map for per-tool output redaction
            output_schema_map: dict[str, dict] = {}
            for module_id in registry.list(tags=self._tags, prefix=self._prefix):
                descriptor = registry.get_definition(module_id)
                if descriptor is not None:
                    schema = getattr(descriptor, "output_schema", None)
                    if schema:
                        output_schema_map[module_id] = schema

            factory = MCPServerFactory()
            server = factory.create_server(name=self._name, version=version)
            tools = factory.build_tools(registry, tags=self._tags, prefix=self._prefix)
            router = ExecutionRouter(
                executor,
                validate_inputs=self._validate_inputs,
                output_schema_map=output_schema_map,
            )

            async_bridge = None
            if self._async_tasks:
                from apcore.async_task import AsyncTaskManager

                from apcore_mcp.server.async_task_bridge import AsyncTaskBridge

                async_bridge = AsyncTaskBridge(
                    AsyncTaskManager(
                        executor,
                        max_concurrent=self._async_max_concurrent,
                        max_tasks=self._async_max_tasks,
                    )
                )

            factory.register_handlers(
                server,
                tools,
                router,
                async_bridge=async_bridge,
                descriptor_lookup=registry.get_definition if self._async_tasks else None,
            )
            factory.register_resource_handlers(server, registry)
            init_options = factory.build_init_options(
                server,
                name=self._name,
                version=version,
            )

            # Build auth middleware for HTTP transports
            auth_middleware: list[tuple[type, dict]] | None = None
            if self._authenticator is not None and self._transport in ("streamable-http", "sse"):
                from apcore_mcp.auth import AuthMiddleware

                mw_kwargs: dict[str, object] = {"authenticator": self._authenticator}
                if not self._require_auth:
                    mw_kwargs["require_auth"] = False
                if self._exempt_paths is not None:
                    mw_kwargs["exempt_paths"] = self._exempt_paths
                auth_middleware = [(AuthMiddleware, mw_kwargs)]

            transport_manager = TransportManager(metrics_collector=self._metrics_collector)
            transport_manager.set_module_count(len(tools))

            self._loop = asyncio.new_event_loop()
            self._started.set()

            try:
                if self._transport == "stdio":
                    self._loop.run_until_complete(
                        transport_manager.run_stdio(server, init_options),
                    )
                elif self._transport == "streamable-http":
                    self._loop.run_until_complete(
                        transport_manager.run_streamable_http(
                            server,
                            init_options,
                            host=self._host,
                            port=self._port,
                            middleware=auth_middleware,
                        ),
                    )
                elif self._transport == "sse":
                    self._loop.run_until_complete(
                        transport_manager.run_sse(
                            server,
                            init_options,
                            host=self._host,
                            port=self._port,
                            middleware=auth_middleware,
                        ),
                    )
            finally:
                self._loop.close()
                self._stopped.set()
        except Exception as exc:
            self._start_error = exc
            self._started.set()  # unblock start() so it can surface the error
