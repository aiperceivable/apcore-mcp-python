"""Non-blocking MCP server wrapper for framework integrations."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apcore_mcp.auth.protocol import Authenticator
    from apcore_mcp.server.transport import MetricsExporter

from apcore_mcp._stdio import output_scope

logger = logging.getLogger(__name__)


_VALID_TRANSPORTS = frozenset({"stdio", "streamable-http", "sse"})


class MCPServer:
    """Non-blocking MCP server.

    Runs the MCP server in a background daemon thread so the caller's main
    thread stays free — the typical shape for embedding an MCP endpoint inside
    a larger application or framework.

    Capability parity: this wrapper delegates all pipeline assembly to
    :class:`~apcore_mcp.APCoreMCP`, so it supports the same feature surface as
    the blocking :func:`~apcore_mcp.serve` entry point — output formatting,
    pipeline strategy, observability, output redaction, pipeline tracing, the
    Tool Explorer, runtime approval (handler / store / notify), caller
    middleware, ACLs, and dynamic registration. The [TM-4] async-task bridge
    is wired automatically so client disconnects mass-cancel session tasks.

    Usage:
        server = MCPServer(registry, transport="streamable-http", port=8000)
        server.start()
        print(f"Server running at {server.address}")
        server.wait()  # blocks until shutdown
    """

    def __init__(
        self,
        registry_or_executor: object | None = None,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        name: str = "apcore-mcp",
        version: str | None = None,
        validate_inputs: bool = False,
        metrics_collector: MetricsExporter | bool | None = None,
        tags: list[str] | None = None,
        prefix: str | None = None,
        log_level: str | None = None,
        authenticator: Authenticator | None = None,
        require_auth: bool = True,
        exempt_paths: set[str] | None = None,
        approval_handler: object | None = None,
        approval_store: object | None = None,
        approval_notify: object | None = None,
        output_formatter: Callable[[dict], str] | None = None,
        output_format: str | None = None,
        strategy: str | None = None,
        redact_output: bool = True,
        trace: bool = False,
        dynamic: bool = False,
        middleware: list[object] | None = None,
        acl: object | None = None,
        observability: bool = False,
        explorer: bool = False,
        explorer_prefix: str = "/explorer",
        allow_execute: bool = False,
        explorer_title: str = "APCore MCP Explorer",
        explorer_project_name: str | None = "apcore-mcp",
        explorer_project_url: str | None = "https://github.com/aiperceivable/apcore-mcp-python",
        async_tasks: bool = True,
        async_max_concurrent: int = 10,
        async_max_tasks: int = 1000,
    ) -> None:
        transport_lower = transport.lower()
        if transport_lower not in _VALID_TRANSPORTS:
            raise ValueError(f"Unknown transport: {transport!r}. Expected one of {sorted(_VALID_TRANSPORTS)}")
        if explorer and not explorer_prefix.startswith("/"):
            raise ValueError("explorer_prefix must start with '/'")
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
        self._log_level = log_level
        self._authenticator = authenticator
        self._require_auth = require_auth
        self._exempt_paths = exempt_paths
        self._approval_handler = approval_handler
        self._approval_store = approval_store
        self._approval_notify = approval_notify
        self._output_formatter = output_formatter
        self._output_format = output_format
        self._strategy = strategy
        self._redact_output = redact_output
        self._trace = trace
        self._dynamic = dynamic
        self._middleware = middleware
        self._acl = acl
        self._observability = observability
        self._explorer = explorer
        self._explorer_prefix = explorer_prefix
        self._allow_execute = allow_execute
        self._explorer_title = explorer_title
        self._explorer_project_name = explorer_project_name
        self._explorer_project_url = explorer_project_url
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

    def _build_app(self) -> Any:
        """Construct the configured :class:`APCoreMCP` for this server.

        Centralised so the full capability set (approval, middleware, acl,
        observability, formatter, redaction, trace, dynamic, explorer) is
        assembled in exactly one place and stays in lock-step with the
        blocking ``serve()`` entry point.
        """
        from apcore_mcp.apcore_mcp import APCoreMCP

        return APCoreMCP(
            self._registry_or_executor,
            name=self._name,
            version=self._version,
            tags=self._tags,
            prefix=self._prefix,
            log_level=self._log_level,
            validate_inputs=self._validate_inputs,
            metrics_collector=self._metrics_collector,
            authenticator=self._authenticator,
            require_auth=self._require_auth,
            exempt_paths=self._exempt_paths,
            approval_handler=self._approval_handler,
            approval_store=self._approval_store,
            approval_notify=self._approval_notify,
            output_formatter=self._output_formatter,
            output_format=self._output_format,
            strategy=self._strategy,
            redact_output=self._redact_output,
            trace=self._trace,
            dynamic=self._dynamic,
            middleware=self._middleware,
            acl=self._acl,
            observability=self._observability,
            async_tasks=self._async_tasks,
            async_max_concurrent=self._async_max_concurrent,
            async_max_tasks=self._async_max_tasks,
        )

    def _run(self) -> None:
        """Internal: run the server event loop on this background thread."""
        try:
            with output_scope(self._transport):
                mcp = self._build_app()
                run_coro = mcp._build_serve_coro(
                    transport=self._transport,
                    host=self._host,
                    port=self._port,
                    explorer=self._explorer,
                    explorer_prefix=self._explorer_prefix,
                    allow_execute=self._allow_execute,
                    explorer_title=self._explorer_title,
                    explorer_project_name=self._explorer_project_name,
                    explorer_project_url=self._explorer_project_url,
                )

                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._started.set()

                serve_task = self._loop.create_task(run_coro())
                try:
                    self._loop.run_until_complete(serve_task)
                finally:
                    try:
                        self._loop.run_until_complete(self._finalize_loop(serve_task))
                    finally:
                        self._loop.close()
        except Exception as exc:
            self._start_error = exc
            self._started.set()  # unblock start() so it can surface the error
        finally:
            self._stopped.set()

    async def _finalize_loop(self, serve_task: asyncio.Task[None]) -> None:
        """Finish owned-loop cleanup before releasing process stdout.

        Cancel the serving task first so transport/session cleanup can await
        its managed tasks without a second simultaneous cancellation. Then
        finish remaining tasks, async generators and executor workers, as an
        asyncio.run() lifetime does for the blocking convenience API.
        """
        if not serve_task.done():
            serve_task.cancel()
            await asyncio.gather(serve_task, return_exceptions=True)
        current_task = asyncio.current_task()
        pending = asyncio.all_tasks() - {current_task}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        loop = asyncio.get_running_loop()
        for task in pending:
            if not task.cancelled() and task.exception() is not None:
                loop.call_exception_handler(
                    {
                        "message": "Unhandled exception during MCPServer shutdown",
                        "exception": task.exception(),
                        "task": task,
                    }
                )
        await loop.shutdown_asyncgens()
        await loop.shutdown_default_executor()
