"""apcore-mcp: Automatic MCP Server & OpenAI Tools Bridge for apcore."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

from starlette.applications import Starlette
from starlette.routing import Mount, Route

from apcore_mcp._utils import resolve_executor, resolve_registry
from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.approval import ElicitationApprovalHandler
from apcore_mcp.adapters.errors import ErrorMapper
from apcore_mcp.adapters.formatter import MCPErrorFormatter, register_mcp_formatter
from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer
from apcore_mcp.adapters.schema import SchemaConverter
from apcore_mcp.auth import Authenticator, AuthMiddleware, ClaimMapping, JWTAuthenticator
from apcore_mcp.config import MCP_DEFAULTS, MCP_ENV_PREFIX, MCP_NAMESPACE, register_mcp_namespace
from apcore_mcp.constants import APCORE_EVENTS, ERROR_CODES, MODULE_ID_PATTERN, REGISTRY_EVENTS
from apcore_mcp.converters.openai import OpenAIConverter
from apcore_mcp.helpers import MCP_ELICIT_KEY, MCP_PROGRESS_KEY, ElicitResult, elicit, report_progress
from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.listener import RegistryListener
from apcore_mcp.server.router import ExecutionRouter
from apcore_mcp.server.server import MCPServer
from apcore_mcp.server.transport import MetricsExporter, TransportManager

try:
    __version__ = _get_version("apcore_mcp")
except PackageNotFoundError:
    __version__ = "unknown"

# Register MCP config namespace and error formatter at import time (idempotent)
register_mcp_namespace()
register_mcp_formatter()

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
    "MCPErrorFormatter",
    "ModuleIDNormalizer",
    # Converters
    "OpenAIConverter",
    # Config Bus
    "MCP_NAMESPACE",
    "MCP_ENV_PREFIX",
    "MCP_DEFAULTS",
    "register_mcp_namespace",
    "register_mcp_formatter",
    # Constants
    "REGISTRY_EVENTS",
    "ERROR_CODES",
    "MODULE_ID_PATTERN",
    "APCORE_EVENTS",
    # Extension helpers
    "report_progress",
    "elicit",
    "ElicitResult",
    "MCP_PROGRESS_KEY",
    "MCP_ELICIT_KEY",
]


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
    metrics_collector: MetricsExporter | bool | None = None,
    explorer: bool = False,
    explorer_prefix: str = "/explorer",
    allow_execute: bool = False,
    explorer_title: str = "MCP Tool Explorer",
    explorer_project_name: str | None = None,
    explorer_project_url: str | None = None,
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    output_formatter: Callable | None = None,
    strategy: str | None = None,
    redact_output: bool = True,
    trace: bool = False,
    middleware: list[object] | None = None,
    acl: object | None = None,
    observability: bool = False,
    async_tasks: bool = True,
    async_max_concurrent: int = 10,
    async_max_tasks: int = 1000,
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
        dynamic: Enable dynamic tool registration via RegistryListener.
        validate_inputs: Validate tool inputs against schemas before execution.
        metrics_collector: Optional MetricsCollector for Prometheus /metrics endpoint.
        explorer: Enable the browser-based Tool Explorer UI (HTTP transports only).
        explorer_prefix: URL prefix for the explorer (default: "/explorer").
        allow_execute: Allow tool execution from the explorer UI.
        explorer_title: Page title for the explorer UI.
        explorer_project_name: Project name shown in the explorer footer.
        explorer_project_url: Project URL linked in the explorer footer.
        authenticator: Optional Authenticator for JWT/token-based auth (HTTP transports only).
        require_auth: If True, unauthenticated requests receive 401.
            If False, requests proceed without identity (permissive mode).
        exempt_paths: Exact paths that bypass authentication.
        approval_handler: Optional approval handler for runtime approval support.
        output_formatter: Optional callable ``(dict) -> str`` that formats execution
            results into text for LLM consumption. When None (default), results
            are serialised with ``json.dumps``. Use ``apcore_toolkit.to_markdown``
            for human-readable Markdown output (install with
            ``pip install apcore-mcp[markdown]``).
        strategy: Pipeline execution strategy. Valid values: "standard",
            "internal", "testing", "performance", "minimal". Ignored when
            an Executor is provided directly.
        redact_output: Redact sensitive fields from tool outputs using
            ``apcore.redact_sensitive()``. Defaults to True.
        trace: Enable pipeline trace capture via ``call_async_with_trace()``.
            When True, trace information is appended to tool call results.
        middleware: Optional list of apcore ``Middleware`` instances to install
            on the Executor via ``executor.use()``. Appended to any middleware
            declared under Config Bus key ``mcp.middleware``. Execution order
            inside the chain is determined by ``Middleware.priority`` (higher
            runs first; equal priorities preserve registration order), so
            merging is additive rather than strictly pre/post.
            Works with both new and pre-existing Executor inputs.
        acl: Optional apcore ``ACL`` instance to install via ``executor.set_acl()``.
            When omitted, the bridge falls back to any ACL declared under Config
            Bus key ``mcp.acl`` (rules + default_effect). Caller-supplied ACL
            takes precedence over Config Bus. Default behavior (no ACL) allows
            all callers.
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

    # Check Config Bus for YAML pipeline / middleware / ACL configuration.
    # Only catch ImportError — apcore may not be installed or may be an older version.
    # Builder ValueErrors (malformed YAML) must propagate so misconfiguration fails
    # loudly at startup, as the builders' docstrings promise.
    pipeline_strategy = None
    config_middleware: list[object] = []
    config_acl: object | None = None
    _config_bus_loaded = False
    try:
        from apcore import Config, build_strategy_from_config

        _config_bus_loaded = True
    except ImportError as exc:
        logger.debug("Config Bus not available, skipping: %s", exc)
    if _config_bus_loaded:
        config = Config.load() if Config is not None else None  # type: ignore[possibly-undefined]
        if config:
            pipeline_config = config.get("mcp.pipeline")
            if pipeline_config and isinstance(pipeline_config, dict) and build_strategy_from_config is not None:
                pipeline_strategy = build_strategy_from_config(pipeline_config, registry=registry)
                if strategy:
                    logger.warning("YAML pipeline config overrides strategy parameter")
            # Load declarative middleware from Config Bus (`mcp.middleware`).
            mw_config = config.get("mcp.middleware")
            if mw_config and isinstance(mw_config, list):
                from apcore_mcp.middleware_builder import build_middleware_from_config

                config_middleware = build_middleware_from_config(mw_config)
            # Load declarative ACL from Config Bus (`mcp.acl`).
            acl_config = config.get("mcp.acl")
            if acl_config:
                from apcore_mcp.acl_builder import build_acl_from_config

                config_acl = build_acl_from_config(acl_config)

            # Wire MCP_DEFAULTS declared keys as Config Bus fallbacks.
            # Config Bus values act as a layer between the function signature defaults
            # and the caller's explicit kwargs. Since Python cannot distinguish "caller
            # passed the default value" from "caller passed nothing", Config Bus values
            # unconditionally override the function defaults — callers who need a
            # specific value must pass it explicitly, which also overrides any Config Bus
            # setting at the next layer of configuration.
            cfg_transport = config.get("mcp.transport")
            if cfg_transport and isinstance(cfg_transport, str):
                transport = cfg_transport  # noqa: F841 — re-bound intentionally
            cfg_host = config.get("mcp.host")
            if cfg_host and isinstance(cfg_host, str):
                host = cfg_host
            cfg_port = config.get("mcp.port")
            if cfg_port is not None:
                try:
                    port = int(cfg_port)
                except (ValueError, TypeError):
                    logger.warning(
                        "mcp.port Config Bus value %r is not an integer, using default %d",
                        cfg_port,
                        port,
                    )
            cfg_name = config.get("mcp.name")
            if cfg_name and isinstance(cfg_name, str):
                name = cfg_name
            cfg_log_level = config.get("mcp.log_level")
            if cfg_log_level and isinstance(cfg_log_level, str):
                log_level = cfg_log_level
            cfg_validate = config.get("mcp.validate_inputs")
            if cfg_validate is not None and isinstance(cfg_validate, bool):
                validate_inputs = cfg_validate
            cfg_explorer = config.get("mcp.explorer")
            if cfg_explorer is not None and isinstance(cfg_explorer, bool):
                explorer = cfg_explorer
            cfg_explorer_prefix = config.get("mcp.explorer_prefix")
            if cfg_explorer_prefix and isinstance(cfg_explorer_prefix, str):
                explorer_prefix = cfg_explorer_prefix
            cfg_require_auth = config.get("mcp.require_auth")
            if cfg_require_auth is not None and isinstance(cfg_require_auth, bool):
                require_auth = cfg_require_auth

    # Merge Config Bus middleware (applied first) with caller-supplied middleware.
    combined_middleware: list[object] = list(config_middleware)
    if middleware:
        combined_middleware.extend(middleware)

    # Caller-supplied ACL wins over Config Bus — mirrors common precedence for
    # security-critical settings (explicit argument > environment).
    effective_acl = acl if acl is not None else config_acl
    if acl is not None and config_acl is not None:
        logger.info("Caller-supplied acl argument overrides Config Bus `mcp.acl`")

    if strategy is not None and hasattr(registry_or_executor, "call_async"):
        logger.warning("strategy parameter ignored when Executor is provided")
    executor = resolve_executor(
        registry_or_executor,
        approval_handler=approval_handler,
        strategy=pipeline_strategy or strategy,
        middleware=combined_middleware,
        acl=effective_acl,
    )

    # Observability auto-wiring (F-033/F-034). ``metrics_collector=True`` or
    # ``observability=True`` provisions apcore's default MetricsCollector +
    # MetricsMiddleware (and Usage when ``observability`` is on). An existing
    # MetricsExporter object is left untouched — back-compat.
    resolved_metrics: MetricsExporter | None
    usage_collector: object | None = None
    if observability or metrics_collector is True:
        from apcore.observability import (
            MetricsCollector,
            MetricsMiddleware,
            UsageCollector,
            UsageMiddleware,
        )

        # retain user object when both flags present; otherwise auto-create
        auto_mc = MetricsCollector() if metrics_collector is True or metrics_collector is None else metrics_collector
        resolved_metrics = auto_mc
        if hasattr(executor, "use"):
            executor.use(MetricsMiddleware(auto_mc))
            if observability:
                uc = UsageCollector()
                executor.use(UsageMiddleware(uc))
                usage_collector = uc
    else:
        resolved_metrics = metrics_collector if metrics_collector is not True else None

    # Build output_schema_map for redaction
    output_schema_map: dict[str, dict] = {}
    if redact_output:
        for module_id in registry.list(tags=tags, prefix=prefix):
            descriptor = registry.get_definition(module_id)
            if descriptor is not None:
                schema = getattr(descriptor, "output_schema", None)
                if schema:
                    output_schema_map[module_id] = schema

    # Build MCP server components
    factory = MCPServerFactory()
    server = factory.create_server(name=name, version=version)
    tools = factory.build_tools(registry, tags=tags, prefix=prefix)
    router = ExecutionRouter(
        executor,
        validate_inputs=validate_inputs,
        output_formatter=output_formatter,
        redact_output=redact_output,
        output_schema_map=output_schema_map,
        trace=trace,
    )

    async_bridge = None
    if async_tasks:
        from apcore.async_task import AsyncTaskManager

        from apcore_mcp.server.async_task_bridge import AsyncTaskBridge

        async_mgr = AsyncTaskManager(
            executor,
            max_concurrent=async_max_concurrent,
            max_tasks=async_max_tasks,
        )
        async_bridge = AsyncTaskBridge(async_mgr)

    factory.register_handlers(
        server,
        tools,
        router,
        async_bridge=async_bridge,
        descriptor_lookup=registry.get_definition if async_tasks else None,
    )
    factory.register_resource_handlers(server, registry)
    init_options = factory.build_init_options(server, name=name, version=version)

    # Start dynamic tool registration listener
    if dynamic:
        listener = RegistryListener(registry, factory)
        listener.start()
        logger.info("RegistryListener started for dynamic tool registration")

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
                title=explorer_title,
                project_name=explorer_project_name,
                project_url=explorer_project_url,
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
    transport_manager = TransportManager(metrics_collector=resolved_metrics)
    transport_manager.set_module_count(len(tools))
    if usage_collector is not None and extra_routes is not None:
        # Explorer UI usage endpoint — surfaces ModuleUsageSummary / detail via JSON.
        from apcore_mcp.explorer import create_usage_routes

        extra_routes.extend(create_usage_routes(usage_collector, prefix=explorer_prefix))

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
    metrics_collector: MetricsExporter | bool | None = None,
    explorer: bool = False,
    explorer_prefix: str = "/explorer",
    allow_execute: bool = False,
    explorer_title: str = "MCP Tool Explorer",
    explorer_project_name: str | None = None,
    explorer_project_url: str | None = None,
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    output_formatter: Callable | None = None,
    strategy: str | None = None,
    trace: bool = False,
    redact_output: bool = True,
    middleware: list[object] | None = None,
    acl: object | None = None,
    observability: bool = False,
    async_tasks: bool = True,
    async_max_concurrent: int = 10,
    async_max_tasks: int = 1000,
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
        explorer_title: Page title for the explorer UI.
        explorer_project_name: Project name shown in the explorer footer.
        explorer_project_url: Project URL linked in the explorer footer.
        authenticator: Optional Authenticator for JWT/token-based auth.
        require_auth: If True, unauthenticated requests receive 401.
        exempt_paths: Exact paths that bypass authentication.
        approval_handler: Optional approval handler for runtime approval.
        output_formatter: Optional callable ``(dict) -> str`` for formatting results.
        strategy: Pipeline execution strategy ("standard", "internal", "testing", "performance", "minimal").
        trace: Enable PipelineTrace capture via call_async_with_trace().
        redact_output: Apply redact_sensitive() to output before serialization.

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

    # Load middleware + ACL from Config Bus (mirrors serve() behaviour).
    # Only catch ImportError; builder ValueErrors propagate to fail loudly on bad config.
    config_middleware: list[object] = []
    config_acl: object | None = None
    _async_config_bus_loaded = False
    try:
        from apcore import Config as _AsyncConfig

        _async_config_bus_loaded = True
    except ImportError as exc:
        logger.debug("Config Bus not available, skipping: %s", exc)
    if _async_config_bus_loaded:
        _cfg = _AsyncConfig.load() if _AsyncConfig is not None else None  # type: ignore[possibly-undefined]
        if _cfg:
            _mw_cfg = _cfg.get("mcp.middleware")
            if _mw_cfg and isinstance(_mw_cfg, list):
                from apcore_mcp.middleware_builder import build_middleware_from_config as _build_mw

                config_middleware = _build_mw(_mw_cfg)
            _acl_cfg = _cfg.get("mcp.acl")
            if _acl_cfg:
                from apcore_mcp.acl_builder import build_acl_from_config as _build_acl

                config_acl = _build_acl(_acl_cfg)

            # Re-bind transport-config keys from Config Bus — mirrors serve() behaviour.
            # These keys are silently ignored when coming from environment variables if we
            # don't re-bind here, because Config Bus returns strings for env-var values.
            _cfg_name = _cfg.get("mcp.name")
            if _cfg_name and isinstance(_cfg_name, str):
                name = _cfg_name
            _cfg_log_level = _cfg.get("mcp.log_level")
            if _cfg_log_level and isinstance(_cfg_log_level, str):
                log_level = _cfg_log_level
            _cfg_validate = _cfg.get("mcp.validate_inputs")
            if _cfg_validate is not None and isinstance(_cfg_validate, bool):
                validate_inputs = _cfg_validate
            _cfg_explorer = _cfg.get("mcp.explorer")
            if _cfg_explorer is not None and isinstance(_cfg_explorer, bool):
                explorer = _cfg_explorer
            _cfg_explorer_prefix = _cfg.get("mcp.explorer_prefix")
            if _cfg_explorer_prefix and isinstance(_cfg_explorer_prefix, str):
                explorer_prefix = _cfg_explorer_prefix
            _cfg_require_auth = _cfg.get("mcp.require_auth")
            if _cfg_require_auth is not None and isinstance(_cfg_require_auth, bool):
                require_auth = _cfg_require_auth

    combined_middleware: list[object] = list(config_middleware)
    if middleware:
        combined_middleware.extend(middleware)
    effective_acl = acl if acl is not None else config_acl
    if acl is not None and config_acl is not None:
        logger.info("Caller-supplied acl argument overrides Config Bus `mcp.acl`")

    # F-036: strategy parameter
    if hasattr(registry_or_executor, "call_async") and strategy is not None:
        logger.warning("strategy parameter ignored when Executor is provided")
    executor = resolve_executor(
        registry_or_executor,
        approval_handler=approval_handler,
        strategy=strategy,
        middleware=combined_middleware,
        acl=effective_acl,
    )

    # Observability auto-wiring (see serve()).
    resolved_metrics: MetricsExporter | None
    usage_collector: object | None = None
    if observability or metrics_collector is True:
        from apcore.observability import (
            MetricsCollector,
            MetricsMiddleware,
            UsageCollector,
            UsageMiddleware,
        )

        auto_mc = MetricsCollector() if metrics_collector is True or metrics_collector is None else metrics_collector
        resolved_metrics = auto_mc
        if hasattr(executor, "use"):
            executor.use(MetricsMiddleware(auto_mc))
            if observability:
                uc = UsageCollector()
                executor.use(UsageMiddleware(uc))
                usage_collector = uc
    else:
        resolved_metrics = metrics_collector if metrics_collector is not True else None

    # F-038: Build output schema map for redaction
    output_schema_map: dict[str, dict] = {}
    if redact_output:
        for mid in registry.list(tags=tags, prefix=prefix):
            desc = registry.get_definition(mid)
            if desc and getattr(desc, "output_schema", None):
                output_schema_map[mid] = desc.output_schema

    # Build MCP server components
    factory = MCPServerFactory()
    server = factory.create_server(name=name, version=resolved_version)
    tools = factory.build_tools(registry, tags=tags, prefix=prefix)
    router = ExecutionRouter(
        executor,
        validate_inputs=validate_inputs,
        output_formatter=output_formatter,
        redact_output=redact_output,
        trace=trace,
        output_schema_map=output_schema_map,
    )

    async_bridge = None
    if async_tasks:
        from apcore.async_task import AsyncTaskManager

        from apcore_mcp.server.async_task_bridge import AsyncTaskBridge

        async_bridge = AsyncTaskBridge(
            AsyncTaskManager(
                executor,
                max_concurrent=async_max_concurrent,
                max_tasks=async_max_tasks,
            )
        )

    factory.register_handlers(
        server,
        tools,
        router,
        async_bridge=async_bridge,
        descriptor_lookup=registry.get_definition if async_tasks else None,
    )
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
                title=explorer_title,
                project_name=explorer_project_name,
                project_url=explorer_project_url,
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

    transport_manager = TransportManager(metrics_collector=resolved_metrics)
    transport_manager.set_module_count(len(tools))
    if usage_collector is not None and extra_routes is not None:
        from apcore_mcp.explorer import create_usage_routes

        extra_routes.extend(create_usage_routes(usage_collector, prefix=explorer_prefix))

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
