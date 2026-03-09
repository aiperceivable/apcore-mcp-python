"""apcore-mcp: Automatic MCP Server & OpenAI Tools Bridge for apcore."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable

from starlette.applications import Starlette
from starlette.routing import Mount, Route

from apcore_mcp._utils import resolve_executor, resolve_registry
from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.approval import ElicitationApprovalHandler
from apcore_mcp.adapters.errors import ErrorMapper
from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer
from apcore_mcp.adapters.schema import SchemaConverter
from apcore_mcp.auth import Authenticator, AuthMiddleware, ClaimMapping, JWTAuthenticator
from apcore_mcp.constants import ERROR_CODES, MODULE_ID_PATTERN, REGISTRY_EVENTS
from apcore_mcp.converters.openai import OpenAIConverter
from apcore_mcp.helpers import MCP_ELICIT_KEY, MCP_PROGRESS_KEY, ElicitResult, elicit, report_progress
from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.listener import RegistryListener
from apcore_mcp.server.router import ExecutionRouter
from apcore_mcp.server.server import MCPServer
from apcore_mcp.server.transport import MetricsExporter, TransportManager

__all__ = [
    # Public API
    "APCoreMCP",
    "serve",
    "async_serve",
    "to_openai_tools",
    # Server building blocks
    "MetricsExporter",
    "MCPServer",
    "MCPServerFactory",
    "ExecutionRouter",
    "RegistryListener",
    "TransportManager",
    # Authentication
    "Authenticator",
    "JWTAuthenticator",
    "ClaimMapping",
    "AuthMiddleware",
    # Adapters
    "AnnotationMapper",
    "ElicitationApprovalHandler",
    "SchemaConverter",
    "ErrorMapper",
    "ModuleIDNormalizer",
    # Converters
    "OpenAIConverter",
    # Constants
    "REGISTRY_EVENTS",
    "ERROR_CODES",
    "MODULE_ID_PATTERN",
    # Extension helpers
    "report_progress",
    "elicit",
    "ElicitResult",
    "MCP_PROGRESS_KEY",
    "MCP_ELICIT_KEY",
]

from apcore_mcp._version import VERSION

__version__ = VERSION


def __getattr__(name: str) -> object:
    if name == "APCoreMCP":
        from apcore_mcp.apcore_mcp import APCoreMCP

        globals()["APCoreMCP"] = APCoreMCP
        return APCoreMCP
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger(__name__)


def serve(
    registry_or_executor: object,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    name: str = "apcore-mcp",
    version: str | None = None,
    on_startup: Callable[[], None] | None = None,
    on_shutdown: Callable[[], None] | None = None,
    tags: list[str] | None = None,
    prefix: str | None = None,
    log_level: str | None = None,
    dynamic: bool = False,
    validate_inputs: bool = False,
    metrics_collector: MetricsExporter | None = None,
    explorer: bool = False,
    explorer_prefix: str = "/explorer",
    allow_execute: bool = False,
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    output_formatter: Callable | None = None,
) -> None:
    """Launch an MCP Server that exposes all apcore modules as tools.

    Args:
        registry_or_executor: An apcore Registry or Executor instance.
        transport: Transport type - "stdio", "streamable-http", or "sse".
        host: Host address for HTTP-based transports.
        port: Port number for HTTP-based transports.
        name: MCP server name.
        version: MCP server version. Defaults to apcore-mcp version.
        on_startup: Optional callback invoked after setup, before transport starts.
        on_shutdown: Optional callback invoked after the transport completes.
        tags: Filter modules by tags. Only modules with ALL specified tags are exposed.
        prefix: Filter modules by ID prefix.
        log_level: Set the log level for the apcore_mcp logger (e.g. "DEBUG", "INFO").
        dynamic: Reserved for future dynamic tool registration support.
        validate_inputs: Validate tool inputs against schemas before execution.
        metrics_collector: Optional MetricsCollector for Prometheus /metrics endpoint.
        explorer: Enable the browser-based Tool Explorer UI (HTTP transports only).
        explorer_prefix: URL prefix for the explorer (default: "/explorer").
        allow_execute: Allow tool execution from the explorer UI.
        authenticator: Optional Authenticator for JWT/token-based auth (HTTP transports only).
        require_auth: If True, unauthenticated requests receive 401.
            If False, requests proceed without identity (permissive mode).
        exempt_paths: Exact paths that bypass authentication.
        approval_handler: Optional approval handler for runtime approval support.
        output_formatter: Optional callable ``(dict) -> str`` that formats execution
            results into text for LLM consumption. When None (default), results
            are serialised with ``json.dumps``. Use ``apcore_toolkit.to_markdown``
            for human-readable Markdown output (this is the default in APCoreMCP).
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
    if explorer and not explorer_prefix.startswith("/"):
        raise ValueError("explorer_prefix must start with '/'")

    version = version or __version__

    if log_level is not None:
        logging.getLogger("apcore_mcp").setLevel(getattr(logging, log_level.upper()))

    registry = resolve_registry(registry_or_executor)
    executor = resolve_executor(registry_or_executor, approval_handler=approval_handler)

    # Build MCP server components
    factory = MCPServerFactory()
    server = factory.create_server(name=name, version=version)
    tools = factory.build_tools(registry, tags=tags, prefix=prefix)
    router = ExecutionRouter(executor, validate_inputs=validate_inputs, output_formatter=output_formatter)
    factory.register_handlers(server, tools, router)
    factory.register_resource_handlers(server, registry)
    init_options = factory.build_init_options(server, name=name, version=version)

    logger.info(
        "Starting MCP server '%s' v%s with %d tools via %s",
        name,
        version,
        len(tools),
        transport,
    )

    # Build optional explorer mount for HTTP transports
    transport_lower = transport.lower()
    extra_routes = None
    if explorer and transport_lower in ("streamable-http", "sse"):
        from apcore_mcp.explorer import create_explorer_mount

        extra_routes = [
            create_explorer_mount(
                tools,
                router,
                allow_execute=allow_execute,
                explorer_prefix=explorer_prefix,
                authenticator=authenticator,
            )
        ]
        logger.info("Tool Explorer enabled at %s", explorer_prefix)

    # Build auth middleware for HTTP transports
    auth_middleware: list[tuple[type, dict]] | None = None
    if authenticator is not None and transport_lower in ("streamable-http", "sse"):
        mw_kwargs: dict[str, object] = {"authenticator": authenticator}
        if not require_auth:
            mw_kwargs["require_auth"] = False
        if exempt_paths is not None:
            mw_kwargs["exempt_paths"] = exempt_paths
        if explorer:
            mw_kwargs["exempt_prefixes"] = {explorer_prefix}
        auth_middleware = [(AuthMiddleware, mw_kwargs)]

    # Select and run transport
    transport_manager = TransportManager(metrics_collector=metrics_collector)
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
    registry_or_executor: object,
    *,
    name: str = "apcore-mcp",
    version: str | None = None,
    tags: list[str] | None = None,
    prefix: str | None = None,
    log_level: str | None = None,
    validate_inputs: bool = False,
    metrics_collector: MetricsExporter | None = None,
    explorer: bool = False,
    explorer_prefix: str = "/explorer",
    allow_execute: bool = False,
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    output_formatter: Callable | None = None,
) -> AsyncIterator[Starlette]:
    """Build an MCP Starlette ASGI app for embedding into a larger service.

    Use this when you want to mount the MCP server alongside other ASGI apps
    (e.g. A2A, Django ASGI) under a single uvicorn process.

    Must be used as an async context manager. The MCP protocol session runs
    as a background task for the lifetime of the context.

    Example::

        async with async_serve(registry) as mcp_app:
            combined = Starlette(routes=[
                Mount("/mcp", app=mcp_app),
                Mount("/a2a", app=a2a_app),
            ])
            config = uvicorn.Config(combined, host="0.0.0.0", port=8000)
            await uvicorn.Server(config).serve()

    Args:
        registry_or_executor: An apcore Registry or Executor instance.
        name: MCP server name.
        version: MCP server version. Defaults to apcore-mcp version.
        tags: Filter modules by tags.
        prefix: Filter modules by ID prefix.
        log_level: Set the log level for the apcore_mcp logger.
        validate_inputs: Validate tool inputs against schemas before execution.
        metrics_collector: Optional MetricsCollector for Prometheus /metrics.
        explorer: Enable the browser-based Tool Explorer UI.
        explorer_prefix: URL prefix for the explorer (default: "/explorer").
        allow_execute: Allow tool execution from the explorer UI.
        authenticator: Optional Authenticator for JWT/token-based auth.
        require_auth: If True, unauthenticated requests receive 401.
        exempt_paths: Exact paths that bypass authentication.
        approval_handler: Optional approval handler for runtime approval.
        output_formatter: Optional callable ``(dict) -> str`` for formatting results.

    Yields:
        A configured Starlette ASGI application with MCP endpoints.
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
    if explorer and not explorer_prefix.startswith("/"):
        raise ValueError("explorer_prefix must start with '/'")

    resolved_version = version or __version__

    if log_level is not None:
        logging.getLogger("apcore_mcp").setLevel(getattr(logging, log_level.upper()))

    registry = resolve_registry(registry_or_executor)
    executor = resolve_executor(registry_or_executor, approval_handler=approval_handler)

    # Build MCP server components
    factory = MCPServerFactory()
    server = factory.create_server(name=name, version=resolved_version)
    tools = factory.build_tools(registry, tags=tags, prefix=prefix)
    router = ExecutionRouter(executor, validate_inputs=validate_inputs, output_formatter=output_formatter)
    factory.register_handlers(server, tools, router)
    factory.register_resource_handlers(server, registry)
    init_options = factory.build_init_options(server, name=name, version=resolved_version)

    logger.info(
        "Building MCP app '%s' v%s with %d tools",
        name,
        resolved_version,
        len(tools),
    )

    # Build optional explorer routes
    extra_routes: list[Route | Mount] | None = None
    if explorer:
        from apcore_mcp.explorer import create_explorer_mount

        extra_routes = [
            create_explorer_mount(
                tools,
                router,
                allow_execute=allow_execute,
                explorer_prefix=explorer_prefix,
                authenticator=authenticator,
            )
        ]
        logger.info("Tool Explorer enabled at %s", explorer_prefix)

    # Build auth middleware
    auth_middleware: list[tuple[type, dict]] | None = None
    if authenticator is not None:
        mw_kwargs: dict[str, object] = {"authenticator": authenticator}
        if not require_auth:
            mw_kwargs["require_auth"] = False
        if exempt_paths is not None:
            mw_kwargs["exempt_paths"] = exempt_paths
        if explorer:
            mw_kwargs["exempt_prefixes"] = {explorer_prefix}
        auth_middleware = [(AuthMiddleware, mw_kwargs)]

    transport_manager = TransportManager(metrics_collector=metrics_collector)
    transport_manager.set_module_count(len(tools))

    async with transport_manager.build_streamable_http_app(
        server,
        init_options,
        extra_routes=extra_routes,
        middleware=auth_middleware,
    ) as app:
        yield app


def to_openai_tools(
    registry_or_executor: object,
    *,
    embed_annotations: bool = False,
    strict: bool = False,
    tags: list[str] | None = None,
    prefix: str | None = None,
) -> list[dict]:
    """Export apcore Registry modules as OpenAI-compatible tool definitions.

    Args:
        registry_or_executor: An apcore Registry or Executor instance.
        embed_annotations: Embed annotation metadata in tool descriptions.
        strict: Add strict: true for OpenAI Structured Outputs.
        tags: Filter modules by tags.
        prefix: Filter modules by ID prefix.

    Returns:
        List of OpenAI tool definition dicts, directly usable with
        openai.chat.completions.create(tools=...).
    """
    registry = resolve_registry(registry_or_executor)
    converter = OpenAIConverter()
    tools = converter.convert_registry(
        registry,
        embed_annotations=embed_annotations,
        strict=strict,
        tags=tags,
        prefix=prefix,
    )
    logger.debug("Converted %d tools to OpenAI format", len(tools))
    return tools
