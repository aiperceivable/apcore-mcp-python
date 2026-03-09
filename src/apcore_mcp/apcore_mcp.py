"""APCoreMCP: Unified entry point for apcore-mcp."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apcore_mcp._utils import resolve_executor, resolve_registry
from apcore_mcp.auth.protocol import Authenticator
from apcore_mcp.server.transport import MetricsExporter

if TYPE_CHECKING:
    from starlette.applications import Starlette

logger = logging.getLogger(__name__)

_USE_DEFAULT_FORMATTER = object()
"""Sentinel to distinguish 'not passed' from 'explicitly set to None'."""


class APCoreMCP:
    """Unified entry point for apcore-mcp.

    Wraps Registry discovery, MCP server creation, and OpenAI tool export
    into a single object with a simple API.

    Examples::

        # Minimal — just point to extensions
        mcp = APCoreMCP("./extensions")
        mcp.serve()

        # With options
        mcp = APCoreMCP("./extensions", name="my-server", tags=["public"])
        mcp.serve(transport="streamable-http", port=9000, explorer=True)

        # Export OpenAI tools
        tools = mcp.to_openai_tools()

        # Embed into ASGI app
        async with mcp.async_serve() as app:
            ...

        # Use existing Registry or Executor
        mcp = APCoreMCP(registry)
        mcp = APCoreMCP(executor)
    """

    def __init__(
        self,
        extensions_dir_or_backend: str | Path | object,
        *,
        name: str = "apcore-mcp",
        version: str | None = None,
        tags: list[str] | None = None,
        prefix: str | None = None,
        log_level: str | None = None,
        validate_inputs: bool = False,
        metrics_collector: MetricsExporter | None = None,
        authenticator: Authenticator | None = None,
        require_auth: bool = True,
        exempt_paths: set[str] | None = None,
        approval_handler: object | None = None,
        output_formatter: Callable[[dict], str] | None | object = _USE_DEFAULT_FORMATTER,
    ) -> None:
        """Create an APCoreMCP instance.

        Args:
            extensions_dir_or_backend: Path to an apcore extensions directory
                (str or Path), or an existing Registry or Executor instance.
            name: MCP server name (max 255 chars).
            version: MCP server version. Defaults to apcore-mcp package version.
            tags: Filter modules by tags. Only modules with ALL specified tags are exposed.
            prefix: Filter modules by ID prefix.
            log_level: Log level for the apcore_mcp logger (e.g. "DEBUG", "INFO").
            validate_inputs: Validate tool inputs against schemas before execution.
            metrics_collector: Optional MetricsExporter for Prometheus /metrics endpoint.
            authenticator: Optional Authenticator for JWT/token-based auth (HTTP only).
            require_auth: If True, unauthenticated requests receive 401.
            exempt_paths: Exact paths that bypass authentication.
            approval_handler: Optional approval handler for runtime approval support.
            output_formatter: Callable that formats dict results into text for
                LLM consumption.  Defaults to ``apcore_toolkit.to_markdown``.
                Set to ``None`` to disable (raw JSON output).
        """
        if not name:
            raise ValueError("name must not be empty")
        if len(name) > 255:
            raise ValueError(f"name exceeds maximum length of 255: {len(name)}")
        if tags is not None:
            for tag in tags:
                if not tag:
                    raise ValueError("Tag values must not be empty")
        if prefix is not None and not prefix:
            raise ValueError("prefix must not be empty")
        if log_level is not None:
            valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
            if log_level.upper() not in valid_levels:
                raise ValueError(f"Unknown log level: {log_level!r}. Valid: {sorted(valid_levels)}")
            logging.getLogger("apcore_mcp").setLevel(getattr(logging, log_level.upper()))

        # Resolve backend: str/Path → Registry with discover(), otherwise pass through
        if isinstance(extensions_dir_or_backend, str | Path):
            from apcore import Registry

            backend: object = Registry(extensions_dir=str(extensions_dir_or_backend))
            backend.discover()  # type: ignore[union-attr]
        else:
            backend = extensions_dir_or_backend

        self._registry = resolve_registry(backend)
        self._executor = resolve_executor(backend, approval_handler=approval_handler)
        self._name = name
        self._version = version
        self._tags = tags
        self._prefix = prefix
        self._validate_inputs = validate_inputs
        self._metrics_collector = metrics_collector
        self._authenticator = authenticator
        self._require_auth = require_auth
        self._exempt_paths = exempt_paths

        # Resolve output formatter: default → to_markdown, None → disabled
        if output_formatter is _USE_DEFAULT_FORMATTER:
            from apcore_toolkit import to_markdown

            self._output_formatter: Callable[[dict], str] | None = to_markdown
        else:
            self._output_formatter = output_formatter  # type: ignore[assignment]

    @property
    def registry(self) -> Any:
        """The underlying apcore Registry."""
        return self._registry

    @property
    def executor(self) -> Any:
        """The underlying apcore Executor."""
        return self._executor

    @property
    def tools(self) -> list[str]:
        """List all discovered module IDs that will be exposed as tools."""
        return list(self._registry.list(tags=self._tags, prefix=self._prefix))

    def _build_server_components(self) -> tuple[Any, Any, list, Any, Any]:
        """Build shared MCP server components used by serve() and async_serve().

        Returns:
            Tuple of (server, router, tools, init_options, version).
        """
        from apcore_mcp._version import VERSION
        from apcore_mcp.server.factory import MCPServerFactory
        from apcore_mcp.server.router import ExecutionRouter

        version = self._version or VERSION

        factory = MCPServerFactory()
        server = factory.create_server(name=self._name, version=version)
        tools = factory.build_tools(self._registry, tags=self._tags, prefix=self._prefix)
        router = ExecutionRouter(
            self._executor,
            validate_inputs=self._validate_inputs,
            output_formatter=self._output_formatter,
        )
        factory.register_handlers(server, tools, router)
        factory.register_resource_handlers(server, self._registry)
        init_options = factory.build_init_options(server, name=self._name, version=version)

        return server, router, tools, init_options, version

    def _build_explorer_routes(
        self,
        tools: list,
        router: Any,
        *,
        allow_execute: bool,
        explorer_prefix: str,
    ) -> list:
        """Build explorer mount routes."""
        from apcore_mcp.explorer import create_explorer_mount

        mount = create_explorer_mount(
            tools,
            router,
            allow_execute=allow_execute,
            explorer_prefix=explorer_prefix,
            authenticator=self._authenticator,
        )
        logger.info("Tool Explorer enabled at %s", explorer_prefix)
        return [mount]

    def _build_auth_middleware(
        self, *, explorer: bool = False, explorer_prefix: str = "/explorer"
    ) -> list[tuple[type, dict]] | None:
        """Build auth middleware list if authenticator is configured."""
        if self._authenticator is None:
            return None

        from apcore_mcp.auth import AuthMiddleware

        mw_kwargs: dict[str, object] = {"authenticator": self._authenticator}
        if not self._require_auth:
            mw_kwargs["require_auth"] = False
        if self._exempt_paths is not None:
            mw_kwargs["exempt_paths"] = self._exempt_paths
        if explorer:
            mw_kwargs["exempt_prefixes"] = {explorer_prefix}
        return [(AuthMiddleware, mw_kwargs)]

    def serve(
        self,
        *,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
        on_startup: Callable[[], None] | None = None,
        on_shutdown: Callable[[], None] | None = None,
        explorer: bool = False,
        explorer_prefix: str = "/explorer",
        allow_execute: bool = False,
    ) -> None:
        """Launch the MCP server (blocking).

        Args:
            transport: Transport type - "stdio", "streamable-http", or "sse".
            host: Host address for HTTP-based transports.
            port: Port number for HTTP-based transports.
            on_startup: Optional callback invoked after setup, before transport starts.
            on_shutdown: Optional callback invoked after the transport completes.
            explorer: Enable the browser-based Tool Explorer UI (HTTP only).
            explorer_prefix: URL prefix for the explorer (default: "/explorer").
            allow_execute: Allow tool execution from the explorer UI.
        """
        from apcore_mcp.server.transport import TransportManager

        if explorer and not explorer_prefix.startswith("/"):
            raise ValueError("explorer_prefix must start with '/'")

        server, router, tools, init_options, version = self._build_server_components()

        logger.info(
            "Starting MCP server '%s' v%s with %d tools via %s",
            self._name,
            version,
            len(tools),
            transport,
        )

        transport_lower = transport.lower()
        extra_routes = None
        if explorer and transport_lower in ("streamable-http", "sse"):
            extra_routes = self._build_explorer_routes(
                tools, router, allow_execute=allow_execute, explorer_prefix=explorer_prefix
            )

        auth_middleware = None
        if transport_lower in ("streamable-http", "sse"):
            auth_middleware = self._build_auth_middleware(explorer=explorer, explorer_prefix=explorer_prefix)

        transport_manager = TransportManager(metrics_collector=self._metrics_collector)
        transport_manager.set_module_count(len(tools))

        async def _run() -> None:
            if transport_lower == "stdio":
                await transport_manager.run_stdio(server, init_options)
            elif transport_lower == "streamable-http":
                await transport_manager.run_streamable_http(
                    server, init_options, host=host, port=port, extra_routes=extra_routes, middleware=auth_middleware
                )
            elif transport_lower == "sse":
                await transport_manager.run_sse(
                    server, init_options, host=host, port=port, extra_routes=extra_routes, middleware=auth_middleware
                )
            else:
                raise ValueError(f"Unknown transport: {transport!r}. Expected 'stdio', 'streamable-http', or 'sse'.")

        if on_startup is not None:
            on_startup()

        try:
            asyncio.run(_run())
        finally:
            if on_shutdown is not None:
                on_shutdown()

    @contextlib.asynccontextmanager
    async def async_serve(
        self,
        *,
        explorer: bool = False,
        explorer_prefix: str = "/explorer",
        allow_execute: bool = False,
    ) -> AsyncIterator[Starlette]:
        """Build an MCP Starlette ASGI app for embedding into a larger service.

        Use this when you want to mount the MCP server alongside other ASGI apps.

        Example::

            async with mcp.async_serve(explorer=True) as mcp_app:
                combined = Starlette(routes=[
                    Mount("/mcp", app=mcp_app),
                    Mount("/a2a", app=a2a_app),
                ])
                config = uvicorn.Config(combined, host="0.0.0.0", port=8000)
                await uvicorn.Server(config).serve()

        Args:
            explorer: Enable the browser-based Tool Explorer UI.
            explorer_prefix: URL prefix for the explorer (default: "/explorer").
            allow_execute: Allow tool execution from the explorer UI.

        Yields:
            A configured Starlette ASGI application with MCP endpoints.
        """
        from apcore_mcp.server.transport import TransportManager

        if explorer and not explorer_prefix.startswith("/"):
            raise ValueError("explorer_prefix must start with '/'")

        server, router, tools, init_options, version = self._build_server_components()

        logger.info(
            "Building MCP app '%s' v%s with %d tools",
            self._name,
            version,
            len(tools),
        )

        extra_routes = None
        if explorer:
            extra_routes = self._build_explorer_routes(
                tools, router, allow_execute=allow_execute, explorer_prefix=explorer_prefix
            )

        auth_middleware = self._build_auth_middleware(explorer=explorer, explorer_prefix=explorer_prefix)

        transport_manager = TransportManager(metrics_collector=self._metrics_collector)
        transport_manager.set_module_count(len(tools))

        async with transport_manager.build_streamable_http_app(
            server,
            init_options,
            extra_routes=extra_routes,
            middleware=auth_middleware,
        ) as app:
            yield app

    def to_openai_tools(
        self,
        *,
        embed_annotations: bool = False,
        strict: bool = False,
    ) -> list[dict]:
        """Export modules as OpenAI-compatible tool definitions.

        Args:
            embed_annotations: Embed annotation metadata in tool descriptions.
            strict: Add strict: true for OpenAI Structured Outputs.

        Returns:
            List of OpenAI tool definition dicts, directly usable with
            openai.chat.completions.create(tools=...).
        """
        from apcore_mcp.converters.openai import OpenAIConverter

        converter = OpenAIConverter()
        tools = converter.convert_registry(
            self._registry,
            embed_annotations=embed_annotations,
            strict=strict,
            tags=self._tags,
            prefix=self._prefix,
        )
        logger.debug("Converted %d tools to OpenAI format", len(tools))
        return tools
