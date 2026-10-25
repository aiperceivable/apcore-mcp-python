"""apcore-mcp: Automatic MCP Server & OpenAI Tools Bridge for apcore.

[D9-001] Top-level ``serve()``, ``async_serve()`` and ``to_openai_tools()``
are thin delegators that forward all kwargs to :class:`APCoreMCP`. Prior
to 0.16.0 the pipeline was duplicated between this module and
``apcore_mcp.py``; the duplication was eliminated to fix latent bugs
(extra_routes typing, metrics_collector narrowing) and to ensure feature
parity in a single place.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version
from typing import Any

from starlette.applications import Starlette

from apcore_mcp._stdio import configured_output
from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.approval import ElicitationApprovalHandler, StorageBackedApprovalHandler
from apcore_mcp.adapters.errors import ErrorMapper, internal_error_response
from apcore_mcp.adapters.formatter import MCPErrorFormatter, McpErrorFormatter, register_mcp_formatter
from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer
from apcore_mcp.adapters.schema import SchemaConverter
from apcore_mcp.apcore_mcp import APCoreMCP
from apcore_mcp.apcore_mcp import (
    _load_config_bus_overrides as _load_config_bus_overrides,  # noqa: F401 - re-export for tests
)
from apcore_mcp.approval_store import ApprovalStore, InMemoryApprovalStore
from apcore_mcp.auth import (
    Authenticator,
    AuthMiddleware,
    ClaimMapping,
    JWTAuthenticator,
    auth_identity_var,
    get_current_identity,
)
from apcore_mcp.config import MCP_DEFAULTS, MCP_ENV_PREFIX, MCP_NAMESPACE, register_mcp_namespace
from apcore_mcp.constants import APCORE_EVENTS, ERROR_CODES, MODULE_ID_PATTERN, REGISTRY_EVENTS
from apcore_mcp.converters.openai import OpenAIConverter
from apcore_mcp.helpers import MCP_ELICIT_KEY, MCP_PROGRESS_KEY, ElicitResult, elicit, report_progress
from apcore_mcp.server.approval_bridge import APPROVAL_META_TOOL_NAMES, ApprovalBridge
from apcore_mcp.server.async_task_bridge import (
    META_TOOL_NAMES,
    AsyncTaskBridge,
)
from apcore_mcp.server.async_task_bridge import (
    RESERVED_PREFIX as APCORE_META_TOOL_PREFIX,
)
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
    # AsyncTaskBridge (parity with TypeScript ``src/index.ts``)
    "AsyncTaskBridge",
    "META_TOOL_NAMES",
    "APCORE_META_TOOL_PREFIX",
    # ApprovalStore and Phase B polling
    "ApprovalStore",
    "InMemoryApprovalStore",
    "StorageBackedApprovalHandler",
    "ApprovalBridge",
    "APPROVAL_META_TOOL_NAMES",
    # Authentication
    "Authenticator",
    "JWTAuthenticator",
    "ClaimMapping",
    "AuthMiddleware",
    # Per-request identity context (cross-SDK parity with TS
    # ``getCurrentIdentity``/``identityStorage`` and Rust ``AUTH_IDENTITY``)
    "auth_identity_var",
    "get_current_identity",
    # Adapters
    "AnnotationMapper",
    "ElicitationApprovalHandler",
    "SchemaConverter",
    "ErrorMapper",
    "MCPErrorFormatter",
    "McpErrorFormatter",
    "ModuleIDNormalizer",
    "internal_error_response",
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


logger = logging.getLogger(__name__)


def serve(
    registry_or_executor: object | None = None,
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
    explorer_title: str = "APCore MCP Explorer",
    explorer_project_name: str | None = "apcore-mcp",
    explorer_project_url: str | None = "https://github.com/aiperceivable/apcore-mcp-python",
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    approval_store: object | None = None,
    approval_notify: object | None = None,
    output_formatter: Callable | None = None,
    output_format: str | None = None,
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

    Thin delegator around :class:`APCoreMCP` — see its documentation for the
    full parameter reference. Kept for backward compatibility; new code is
    encouraged to use :class:`APCoreMCP` directly.

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
        log_level: Set the log level for the apcore_mcp logger.
        dynamic: Enable dynamic tool registration via RegistryListener.
        validate_inputs: Validate tool inputs against schemas before execution.
        metrics_collector: Optional MetricsCollector for Prometheus /metrics endpoint.
        explorer: Enable the browser-based Tool Explorer UI (HTTP transports only).
        explorer_prefix: URL prefix for the explorer (default: "/explorer").
        allow_execute: Allow tool execution from the explorer UI.
        explorer_title: Page title for the explorer UI.
        explorer_project_name: Project name shown in the explorer footer.
        explorer_project_url: Project URL linked in the explorer footer.
        authenticator: Optional Authenticator for JWT/token-based auth.
        require_auth: If True, unauthenticated requests receive 401.
        exempt_paths: Exact paths that bypass authentication.
        approval_handler: Optional approval handler for runtime approval support.
        approval_store: Optional :class:`ApprovalStore` for Phase B async
            approval polling. When given without ``approval_handler``, a
            ``StorageBackedApprovalHandler`` is auto-constructed.
        approval_notify: Optional async callback ``(approval_id, module_id,
            arguments) -> None`` invoked when a Phase B approval is recorded
            pending (fan-out to Slack/email/etc.).
        output_formatter: Optional callable that formats results into text.
        output_format: Optional built-in output format name ("json", "csv", "jsonl").
        strategy: Pipeline execution strategy ("standard", "internal",
            "testing", "performance", "minimal").
        redact_output: Redact sensitive fields from tool outputs.
        trace: Enable pipeline trace capture.
        middleware: Optional list of apcore ``Middleware`` instances.
        acl: Optional apcore ``ACL`` instance.
        observability: Auto-provision metrics + usage middleware when True.
        async_tasks: Enable AsyncTaskBridge meta tools.
        async_max_concurrent: Max concurrent async tasks.
        async_max_tasks: Max total async tasks tracked.
    """
    with configured_output(transport):
        mcp = APCoreMCP(
            registry_or_executor,
            name=name,
            version=version,
            tags=tags,
            prefix=prefix,
            log_level=log_level,
            validate_inputs=validate_inputs,
            metrics_collector=metrics_collector,
            authenticator=authenticator,
            require_auth=require_auth,
            exempt_paths=exempt_paths,
            approval_handler=approval_handler,
            approval_store=approval_store,
            approval_notify=approval_notify,
            output_formatter=output_formatter,
            output_format=output_format,
            strategy=strategy,
            redact_output=redact_output,
            trace=trace,
            dynamic=dynamic,
            middleware=middleware,
            acl=acl,
            observability=observability,
            async_tasks=async_tasks,
            async_max_concurrent=async_max_concurrent,
            async_max_tasks=async_max_tasks,
        )
        mcp.serve(
            transport=transport,
            host=host,
            port=port,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            explorer=explorer,
            explorer_prefix=explorer_prefix,
            allow_execute=allow_execute,
            explorer_title=explorer_title,
            explorer_project_name=explorer_project_name,
            explorer_project_url=explorer_project_url,
        )


def async_serve(
    registry_or_executor: object | None = None,
    *,
    name: str = "apcore-mcp",
    version: str | None = None,
    tags: list[str] | None = None,
    prefix: str | None = None,
    log_level: str | None = None,
    dynamic: bool = False,
    validate_inputs: bool = False,
    metrics_collector: MetricsExporter | bool | None = None,
    explorer: bool = False,
    explorer_prefix: str = "/explorer",
    allow_execute: bool = False,
    explorer_title: str = "APCore MCP Explorer",
    explorer_project_name: str | None = "apcore-mcp",
    explorer_project_url: str | None = "https://github.com/aiperceivable/apcore-mcp-python",
    authenticator: Authenticator | None = None,
    require_auth: bool = True,
    exempt_paths: set[str] | None = None,
    approval_handler: object | None = None,
    approval_store: object | None = None,
    approval_notify: object | None = None,
    output_formatter: Callable | None = None,
    output_format: str | None = None,
    strategy: str | None = None,
    trace: bool = False,
    redact_output: bool = True,
    middleware: list[object] | None = None,
    acl: object | None = None,
    observability: bool = False,
    async_tasks: bool = True,
    async_max_concurrent: int = 10,
    async_max_tasks: int = 1000,
) -> _AsyncServeCtx:
    """Build an MCP Starlette ASGI app for embedding into a larger service.

    Thin delegator around :class:`APCoreMCP.async_serve`. Returns an async
    context manager.

    Use this when you want to mount the MCP server alongside other ASGI apps
    (e.g. A2A, Django ASGI) under a single uvicorn process.

    Example::

        async with async_serve(registry) as mcp_app:
            combined = Starlette(routes=[
                Mount("/mcp", app=mcp_app),
                Mount("/a2a", app=a2a_app),
            ])
            config = uvicorn.Config(combined, host="0.0.0.0", port=8000)
            await uvicorn.Server(config).serve()
    """
    mcp = APCoreMCP(
        registry_or_executor,
        name=name,
        version=version,
        tags=tags,
        prefix=prefix,
        log_level=log_level,
        validate_inputs=validate_inputs,
        metrics_collector=metrics_collector,
        authenticator=authenticator,
        require_auth=require_auth,
        exempt_paths=exempt_paths,
        approval_handler=approval_handler,
        approval_store=approval_store,
        approval_notify=approval_notify,
        output_formatter=output_formatter,
        output_format=output_format,
        strategy=strategy,
        redact_output=redact_output,
        trace=trace,
        dynamic=dynamic,
        middleware=middleware,
        acl=acl,
        observability=observability,
        async_tasks=async_tasks,
        async_max_concurrent=async_max_concurrent,
        async_max_tasks=async_max_tasks,
        _load_pipeline_from_config=False,
    )
    return _AsyncServeCtx(
        mcp,
        explorer=explorer,
        explorer_prefix=explorer_prefix,
        allow_execute=allow_execute,
        explorer_title=explorer_title,
        explorer_project_name=explorer_project_name,
        explorer_project_url=explorer_project_url,
    )


class _AsyncServeCtx:
    """Lightweight async context manager wrapper around ``APCoreMCP.async_serve``.

    We deliberately do not decorate :func:`async_serve` with
    ``@contextlib.asynccontextmanager`` so that callers can introspect the
    object without entering it (matches pre-refactor behaviour where the
    module-level function returned an :class:`AsyncGeneratorContextManager`).
    """

    def __init__(
        self,
        mcp: APCoreMCP,
        *,
        explorer: bool,
        explorer_prefix: str,
        allow_execute: bool,
        explorer_title: str,
        explorer_project_name: str | None,
        explorer_project_url: str | None,
    ) -> None:
        self._mcp = mcp
        self._kwargs: dict[str, Any] = {
            "explorer": explorer,
            "explorer_prefix": explorer_prefix,
            "allow_execute": allow_execute,
            "explorer_title": explorer_title,
            "explorer_project_name": explorer_project_name,
            "explorer_project_url": explorer_project_url,
        }
        self._ctx: Any = None

    async def __aenter__(self) -> Starlette:
        self._ctx = self._mcp.async_serve(**self._kwargs)
        return await self._ctx.__aenter__()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> object:
        assert self._ctx is not None
        return await self._ctx.__aexit__(exc_type, exc, tb)


def to_openai_tools(
    registry_or_executor: object | None = None,
    *,
    embed_annotations: bool = False,
    strict: bool = False,
    tags: list[str] | None = None,
    prefix: str | None = None,
) -> list[dict]:
    """Export apcore Registry modules as OpenAI-compatible tool definitions.

    Thin delegator around :meth:`APCoreMCP.to_openai_tools`.

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
    mcp = APCoreMCP(
        registry_or_executor,
        tags=tags,
        prefix=prefix,
        # Skip async-task / observability machinery — to_openai_tools only
        # needs the registry. Avoid spinning up an AsyncTaskManager.
        async_tasks=False,
        # Avoid Config Bus pipeline construction overhead.
        _load_pipeline_from_config=False,
    )
    return mcp.to_openai_tools(embed_annotations=embed_annotations, strict=strict)
