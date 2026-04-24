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
        metrics_collector: MetricsExporter | bool | None = None,
        authenticator: Authenticator | None = None,
        require_auth: bool = True,
        exempt_paths: set[str] | None = None,
        approval_handler: object | None = None,
        output_formatter: Callable[[dict], str] | None = None,
        middleware: list[object] | None = None,
        acl: object | None = None,
        observability: bool = False,
        async_tasks: bool = True,
        async_max_concurrent: int = 10,
        async_max_tasks: int = 1000,
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
            output_formatter: Optional callable that formats dict results into
                text for LLM consumption.  Defaults to ``None`` (raw JSON).
                Use ``apcore_toolkit.to_markdown`` for Markdown output.
            middleware: Optional list of apcore ``Middleware`` instances to
                install on the Executor via ``executor.use()``. Appended to
                any middleware declared under Config Bus key
                ``mcp.middleware``. Chain execution order is controlled by
                ``Middleware.priority``, not insertion order.
            acl: Optional apcore ``ACL`` instance to install via
                ``executor.set_acl()``. When omitted, the bridge falls back
                to any ACL declared under Config Bus key ``mcp.acl``.
                Caller-supplied ACL takes precedence over Config Bus.
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

        # Load declarative middleware + ACL from Config Bus, then merge with caller args.
        # Only catch ImportError — apcore may not be installed or may be an older version.
        # Builder ValueErrors (malformed YAML) must propagate so misconfiguration fails
        # loudly at startup, as the builders' docstrings promise.
        config_middleware: list[object] = []
        config_acl: object | None = None
        try:
            from apcore import Config
        except ImportError as exc:
            logger.debug("Config Bus not available, skipping: %s", exc)
            Config = None  # type: ignore[assignment]
        if Config is not None:
            config = Config.load() if Config is not None else None
            if config:
                mw_config = config.get("mcp.middleware")
                if mw_config and isinstance(mw_config, list):
                    from apcore_mcp.middleware_builder import build_middleware_from_config

                    config_middleware = build_middleware_from_config(mw_config)
                acl_config = config.get("mcp.acl")
                if acl_config:
                    from apcore_mcp.acl_builder import build_acl_from_config

                    config_acl = build_acl_from_config(acl_config)

        combined_middleware: list[object] = list(config_middleware)
        if middleware:
            combined_middleware.extend(middleware)

        # Caller-supplied ACL wins over Config Bus.
        effective_acl = acl if acl is not None else config_acl

        self._executor = resolve_executor(
            backend,
            approval_handler=approval_handler,
            middleware=combined_middleware,
            acl=effective_acl,
        )

        # Observability: auto-install MetricsMiddleware + UsageMiddleware when requested.
        # ``metrics_collector`` accepts a pre-built MetricsExporter (back-compat) or the
        # sentinel ``True``/``observability=True`` to auto-provision defaults.
        self._usage_collector: Any = None
        resolved_metrics: Any = metrics_collector
        if observability or metrics_collector is True:
            from apcore.observability import (
                MetricsCollector,
                MetricsMiddleware,
                UsageCollector,
                UsageMiddleware,
            )

            # user may supply both collector and observability flag; preserve their object
            mc = MetricsCollector() if metrics_collector is True or metrics_collector is None else metrics_collector
            resolved_metrics = mc
            if hasattr(self._executor, "use"):
                self._executor.use(MetricsMiddleware(mc))
                if observability:
                    uc = UsageCollector()
                    self._executor.use(UsageMiddleware(uc))
                    self._usage_collector = uc

        self._name = name
        self._version = version
        self._tags = tags
        self._prefix = prefix
        self._validate_inputs = validate_inputs
        self._metrics_collector = resolved_metrics
        self._authenticator = authenticator
        self._require_auth = require_auth
        self._exempt_paths = exempt_paths
        self._async_tasks = async_tasks
        self._async_max_concurrent = async_max_concurrent
        self._async_max_tasks = async_max_tasks
        self._async_bridge: Any = None

        self._output_formatter = output_formatter

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
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        from apcore_mcp.server.async_task_bridge import AsyncTaskBridge
        from apcore_mcp.server.factory import MCPServerFactory
        from apcore_mcp.server.router import ExecutionRouter

        try:
            pkg_version = _pkg_version("apcore-mcp")
        except PackageNotFoundError:
            pkg_version = "unknown"
        version = self._version or pkg_version

        factory = MCPServerFactory()
        server = factory.create_server(name=self._name, version=version)
        tools = factory.build_tools(self._registry, tags=self._tags, prefix=self._prefix)

        # Build per-tool output schema map for redaction (matches serve() behaviour)
        output_schema_map: dict[str, dict] = {}
        for mid in self._registry.list(tags=self._tags, prefix=self._prefix):
            desc = self._registry.get_definition(mid)
            if desc is not None:
                schema = getattr(desc, "output_schema", None)
                if schema:
                    output_schema_map[mid] = schema

        router = ExecutionRouter(
            self._executor,
            validate_inputs=self._validate_inputs,
            output_formatter=self._output_formatter,
            redact_output=True,
            output_schema_map=output_schema_map,
        )

        async_bridge: AsyncTaskBridge | None = None
        if self._async_tasks:
            from apcore.async_task import AsyncTaskManager

            mgr = AsyncTaskManager(
                self._executor,
                max_concurrent=self._async_max_concurrent,
                max_tasks=self._async_max_tasks,
            )
            async_bridge = AsyncTaskBridge(mgr)
            self._async_bridge = async_bridge

        descriptor_lookup = self._registry.get_definition if self._async_tasks else None
        factory.register_handlers(
            server,
            tools,
            router,
            async_bridge=async_bridge,
            descriptor_lookup=descriptor_lookup,
        )
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
        explorer_title: str = "APCore MCP Explorer",
        explorer_project_name: str = "apcore-mcp",
        explorer_project_url: str = "https://github.com/aiperceivable/apcore-mcp-python",
    ) -> list:
        """Build explorer mount routes."""
        from apcore_mcp.explorer import create_explorer_mount

        mount = create_explorer_mount(
            tools,
            router,
            allow_execute=allow_execute,
            explorer_prefix=explorer_prefix,
            authenticator=self._authenticator,
            title=explorer_title,
            project_name=explorer_project_name,
            project_url=explorer_project_url,
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
        explorer_title: str = "APCore MCP Explorer",
        explorer_project_name: str = "apcore-mcp",
        explorer_project_url: str = "https://github.com/aiperceivable/apcore-mcp-python",
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
            explorer_title: Page title for the explorer UI.
            explorer_project_name: Project name shown in the explorer footer.
            explorer_project_url: Project URL linked in the explorer footer.
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
                tools,
                router,
                allow_execute=allow_execute,
                explorer_prefix=explorer_prefix,
                explorer_title=explorer_title,
                explorer_project_name=explorer_project_name,
                explorer_project_url=explorer_project_url,
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
        explorer_title: str = "APCore MCP Explorer",
        explorer_project_name: str = "apcore-mcp",
        explorer_project_url: str = "https://github.com/aiperceivable/apcore-mcp-python",
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
            explorer_title: Page title for the explorer UI.
            explorer_project_name: Project name shown in the explorer footer.
            explorer_project_url: Project URL linked in the explorer footer.

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
                tools,
                router,
                allow_execute=allow_execute,
                explorer_prefix=explorer_prefix,
                explorer_title=explorer_title,
                explorer_project_name=explorer_project_name,
                explorer_project_url=explorer_project_url,
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
