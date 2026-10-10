"""MCPServerFactory: create and configure MCP Server with tools from apcore Registry."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, unquote

from apcore.schema.exporter import SchemaExporter
from apcore.schema.types import SchemaDefinition
from mcp import types as mcp_types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

import apcore_mcp.markdown as _markdown
from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.schema import SchemaConverter
from apcore_mcp.auth.middleware import auth_identity_var
from apcore_mcp.server.approval_bridge import ApprovalBridge
from apcore_mcp.server.async_task_bridge import RESERVED_PREFIX, AsyncTaskBridge
from apcore_mcp.server.transport import transport_session_var

logger = logging.getLogger(__name__)


_AI_INTENT_KEYS = ("x-when-to-use", "x-when-not-to-use", "x-common-mistakes", "x-workflow-hints")

# aiperceivable/apcore-mcp#15: the three read-only `system.*` namespaces.
# `system.control.*` is deliberately excluded -- it performs writes and stays
# a Tool. Classification is by module_id prefix ONLY (no adapter-level
# toggle), so it can never drift from what the registry actually holds.
_READONLY_SYSTEM_PREFIXES = ("system.health.", "system.usage.", "system.manifest.")

# The URI scheme this factory reads read-only system.* modules under.
_SYSTEM_RESOURCE_SCHEME = "apcore"

# Summary/full read-only modules -> one static Resource each, keyed by the
# apcore module_id they proxy. `system.usage.summary` additionally accepts an
# optional `?period=` query parameter on read (not advertised as a template
# parameter -- it is a plain optional query string, matching the module's own
# optional `period` input).
_SYSTEM_STATIC_RESOURCE_IDS = (
    "system.health.summary",
    "system.usage.summary",
    "system.manifest.full",
)

# Per-module read-only modules -> one ResourceTemplate each, parameterized by
# `{module_id}` as a URI path segment. `system.usage.module` additionally
# accepts an optional `?period=` query parameter on read.
_SYSTEM_TEMPLATE_RESOURCE_IDS = (
    "system.health.module",
    "system.usage.module",
    "system.manifest.module",
)

# aiperceivable/apcore-mcp#15's URI-convention table declares
# `system.usage.module`'s template as `apcore://system.usage.module/{module_id}{?period}`
# (RFC 6570 form-style query expansion) -- the only one of the three templates
# with an optional query parameter. Cross-checked against apcore-mcp-typescript's
# `systemResourceUriTemplate()`, which appends the same `{?period}` suffix.
_SYSTEM_TEMPLATE_QUERY_SUFFIX = {"system.usage.module": "{?period}"}


# The read-only management modules this adapter actually projects as
# resources. Membership in this set -- not a bare prefix match -- is what
# removes a module from `tools/list`; see `is_readonly_system_module`.
_SYSTEM_RESOURCE_IDS = frozenset(_SYSTEM_STATIC_RESOURCE_IDS + _SYSTEM_TEMPLATE_RESOURCE_IDS)


def is_readonly_system_module(module_id: str) -> bool:
    """Return True when *module_id* is projected as an MCP resource, not a tool.

    Per aiperceivable/apcore-mcp#15, `system.health.*`, `system.usage.*` and
    `system.manifest.*` are read-only and MUST be projected as MCP resources
    rather than tools. `system.control.*` performs writes and is unaffected --
    it is not matched here and keeps its normal Tool projection.

    Matches the six canonical ids exactly rather than the three prefixes. The
    two agree for every registry `register_sys_modules` produces, and differ
    only for a read-only `system.*` id this adapter has no resource for --
    a seventh module added by a future apcore, or one a host registered
    through `register_internal` itself. A prefix match would drop such a
    module from `tools/list` while `register_resource_handlers` (which builds
    from these same canonical ids) gave it no resource either, so it would
    vanish from both discovery surfaces at once. Keeping it a tool is the
    safer failure: visible and callable, merely classified as a tool until
    this adapter learns its resource shape. Discovery is all that is at stake
    -- `tools/call` dispatches by id and never consulted this list.
    """
    return module_id in _SYSTEM_RESOURCE_IDS


# aiperceivable/apcore-mcp#16 Phase A -- the `com.aiperceivable/management`
# initialize-time extension capability, and the stable order its `surfaces`
# array is emitted in.
_MANAGEMENT_EXTENSION_KEY = "com.aiperceivable/management"
_MANAGEMENT_SURFACE_ORDER = ("health", "usage", "manifest", "control")
# apcore's PROTOCOL_SPEC version as of this file's last sync (PROTOCOL_SPEC.md
# header, apcore repo, 2026-08-31: "Version: 1.30.0"). apcore does not export
# this as a runtime constant (`apcore.__version__` is the *package* version,
# a different number), so it must be updated here by hand when the spec the
# management-extension contract (§6.6.5) lives in bumps.
_PROTOCOL_SPEC_VERSION = "1.30.0"


class MCPServerFactory:
    """Creates and configures MCP Server instances from apcore Registry."""

    def __init__(self, *, strict: bool = True, rich_description: bool = False) -> None:
        self._strict = strict
        # When ``rich_description`` is True, MCP Tool descriptions are
        # rendered as apcore-toolkit Markdown (title, description,
        # behavior table, schemas, examples) instead of the plain
        # one-line description. LLMs select tools primarily from this
        # field — Markdown packs more decision-relevant signal per
        # token. Requires apcore-toolkit (install via the
        # ``[markdown]`` extra); falls back to plain text + WARN log
        # when toolkit is unavailable.
        self._rich_description = rich_description
        # One-shot flag so we only log a single "toolkit missing" WARN
        # per factory instance instead of one per descriptor (matches
        # the TS factory's `_warnedToolkitMissing` guard).
        self._warned_toolkit_missing = False
        self._schema_converter = SchemaConverter(strict=strict)
        self._annotation_mapper = AnnotationMapper()
        self._schema_exporter = SchemaExporter()
        from apcore_mcp.adapters.errors import ErrorMapper

        self._error_mapper = ErrorMapper()

    @staticmethod
    async def prepare() -> bool:
        """Cross-SDK parity no-op for ``MCPServerFactory.prepare()``.

        TypeScript's factory uses this to prime the apcore-toolkit Markdown
        renderer (the toolkit's import has measurable startup cost in Node).
        Python imports apcore-toolkit lazily inside the rendering path, so
        no priming is required. Exposed as an awaitable no-op so cross-SDK
        application code can call ``await MCPServerFactory.prepare()`` at
        startup without a language-specific branch. See
        ``docs/features/mcp-server-factory.md`` §"Py/Rust no-op for parity".
        """
        return False

    def create_server(self, name: str = "apcore-mcp", version: str = "0.1.0") -> Server:
        """Create a new MCP low-level Server instance.

        Args:
            name: Server name for identification. Must be non-empty and at most
                255 characters (spec: cross-SDK parity with TS/Rust).
            version: Server version string. Note: the MCP SDK's ``Server``
                constructor only accepts ``name``; ``version`` is surfaced to
                clients through :meth:`build_init_options` / ``InitializationOptions``.

        Returns:
            A configured Server. Handlers are NOT registered yet.

        Raises:
            ValueError: If ``name`` is empty or exceeds 255 characters.
        """
        # [D10-002] Validate name per spec: non-empty, max 255 chars.
        if not name or len(name) > 255:
            raise ValueError(f"Server name must be non-empty and at most 255 chars, got {len(name)} chars")
        return Server(name)

    def build_tool(
        self,
        descriptor: Any,
        *,
        registry: Any | None = None,
        strict: bool | None = None,
    ) -> mcp_types.Tool:
        """Build an MCP Tool from a ModuleDescriptor.

        Mapping:
        - descriptor.module_id -> Tool.name
        - descriptor.description -> Tool.description
        - SchemaConverter.convert_input_schema(descriptor) -> Tool.inputSchema
        - SchemaExporter.export_mcp() -> ToolAnnotations hints (camelCase)
        - AnnotationMapper -> requires_approval, streaming (_meta), title

        Args:
            descriptor: ModuleDescriptor with module_id, description,
                        input_schema, and annotations attributes.

        Returns:
            An MCP Tool object ready for registration.
        """
        # Reject reserved-prefix ids so user modules cannot shadow async meta-tools.
        if getattr(descriptor, "module_id", "").startswith(RESERVED_PREFIX):
            raise ValueError(f"Module id {descriptor.module_id!r} uses reserved prefix {RESERVED_PREFIX!r}")

        if strict is None:
            strict = self._strict

        # [A-D-012] Strict-Schema-Sourcing: prefer the registry's
        # `export_schema(module_id, strict=True)` when available, matching
        # the TypeScript factory and the spec at
        # docs/features/mcp-server-factory.md "Strict Schema Sourcing".
        # Falls back to local SchemaConverter when the registry doesn't
        # expose export_schema or the call fails.
        input_schema: Any | None = None
        if strict and registry is not None and callable(getattr(registry, "export_schema", None)):
            try:
                exported = registry.export_schema(descriptor.module_id, strict=True)
                if isinstance(exported, dict):
                    candidate = exported.get("input_schema") or exported.get("inputSchema")
                    if isinstance(candidate, dict):
                        input_schema = candidate
            except Exception as exc:  # noqa: BLE001 — fall through to local converter
                logger.debug(
                    "registry.export_schema(strict=True) raised for %s; falling back to local converter: %s",
                    descriptor.module_id,
                    exc,
                )
        if input_schema is None:
            # [A-D-FA-2] Pass the resolved per-call flag through. Pre-fix the
            # local converter used its construction-time strict setting, so
            # build_tool(descriptor, strict=False) on a strict factory still
            # emitted a strict schema and the argument only gated whether the
            # registry export_schema path was attempted. TypeScript honours the
            # per-call flag (factory.ts:188).
            input_schema = self._schema_converter.convert_input_schema(descriptor, strict=strict)

        # NOTE: Python uses SchemaExporter.export_mcp() for annotation mapping,
        # while TypeScript uses AnnotationMapper.toMcpAnnotations() directly.
        # Both produce identical output. If annotation logic changes, update both paths.
        schema_def = SchemaDefinition(
            module_id=descriptor.module_id,
            description=descriptor.description,
            input_schema=descriptor.input_schema,
            output_schema=getattr(descriptor, "output_schema", {}),
        )
        exported = self._schema_exporter.export_mcp(schema_def, annotations=descriptor.annotations)
        hints = exported["annotations"]

        tool_annotations = mcp_types.ToolAnnotations(
            readOnlyHint=hints.get("readOnlyHint"),
            destructiveHint=hints.get("destructiveHint"),
            idempotentHint=hints.get("idempotentHint"),
            openWorldHint=hints.get("openWorldHint"),
            title=None,
        )

        # Build optional _meta with requires_approval and streaming hints
        meta: dict[str, object] | None = None
        if self._annotation_mapper.has_requires_approval(descriptor.annotations):
            meta = {"requiresApproval": True}
        # apcore 0.32.0 exports streaming in _meta; read the source annotation
        # directly, as the TypeScript and Rust factories do.
        if getattr(descriptor.annotations, "streaming", False):
            if meta is None:
                meta = {}
            meta["streaming"] = True

        # Resolve display overlay fields (§5.13)
        metadata = getattr(descriptor, "metadata", None) or {}
        display = metadata.get("display") or {}
        mcp_display = display.get("mcp") or {}

        tool_name: str = mcp_display.get("alias") or descriptor.module_id
        # Display-overlay ``mcp.description`` is a hard override the
        # operator typed by hand — it always wins, even when
        # ``rich_description=True`` is enabled. Otherwise, when rich
        # descriptions are on, the LLM-facing description is rendered
        # via apcore-toolkit's ``format_module``.
        if mcp_display.get("description"):
            description = mcp_display["description"]
        elif self._rich_description and _markdown.is_available():
            # render_module_markdown never raises; it returns None when the
            # toolkit is unavailable or could not produce a string. [A-D-MD-3]
            description = _markdown.render_module_markdown(descriptor) or descriptor.description
        else:
            if self._rich_description and not _markdown.is_available() and not self._warned_toolkit_missing:
                self._warned_toolkit_missing = True
                logger.warning(
                    "rich_description: apcore-toolkit not installed; "
                    "install 'apcore-mcp[markdown]' to enable Markdown "
                    "tool descriptions. Falling back to plain descriptions."
                )
            description = descriptor.description

        # Append guidance if present (AI usage hints)
        guidance: str | None = mcp_display.get("guidance")
        if guidance:
            description = f"{description}\n\nGuidance: {guidance}"

        # Append legacy x- AI intent metadata for backward compatibility
        intent_parts = []
        for key in _AI_INTENT_KEYS:
            val = metadata.get(key)
            if val:
                label = key.replace("x-", "").replace("-", " ").title()
                intent_parts.append(f"{label}: {val}")
        if intent_parts:
            description += "\n\n" + "\n".join(intent_parts)

        return mcp_types.Tool(
            name=tool_name,
            description=description,
            inputSchema=input_schema,
            annotations=tool_annotations,
            _meta=meta,
        )

    def build_tools(
        self,
        registry: Any,
        tags: list[str] | None = None,
        prefix: str | None = None,
        *,
        strict: bool | None = None,
    ) -> list[mcp_types.Tool]:
        """Build Tool objects for all modules in a Registry.

        Uses registry.list(tags=tags, prefix=prefix) to discover module IDs,
        then registry.get_definition() to obtain each descriptor. Modules
        whose definition is None are skipped. Errors during build_tool are
        logged as warnings and the module is skipped.

        [aiperceivable/apcore-mcp#15] Read-only `system.*` modules
        (`system.health.*`, `system.usage.*`, `system.manifest.*`) are
        skipped here -- they are projected as MCP resources instead, by
        :meth:`register_resource_handlers`. `system.control.*` is unaffected
        and still becomes a Tool.

        Args:
            registry: An apcore Registry (or compatible stub) with list()
                      and get_definition() methods.
            tags: Optional tag filter passed to registry.list().
            prefix: Optional prefix filter passed to registry.list().

        Returns:
            List of successfully built MCP Tool objects.
        """
        tools: list[mcp_types.Tool] = []
        for module_id in registry.list(tags=tags, prefix=prefix):
            if is_readonly_system_module(module_id):
                continue
            descriptor = registry.get_definition(module_id)
            if descriptor is None:
                logger.warning("Skipped module %s: no definition found", module_id)
                continue
            try:
                # [A-D-012] Pass the registry so build_tool can prefer
                # registry.export_schema(strict=True) over local conversion.
                tools.append(self.build_tool(descriptor, registry=registry, strict=strict))
            except ValueError as e:
                # Reserved-prefix violations are hard config errors — re-raise so
                # misconfiguration is visible at startup rather than silently
                # producing a missing tool.
                if "reserved prefix" in str(e).lower():
                    raise
                logger.warning("Failed to build tool for %s: %s", module_id, e)
                continue
            except Exception as e:
                logger.warning("Failed to build tool for %s: %s", module_id, e)
                continue
        return tools

    def register_handlers(
        self,
        server: Server,
        tools: list[mcp_types.Tool],
        router: Any,
        *,
        async_bridge: AsyncTaskBridge | None = None,
        approval_bridge: ApprovalBridge | None = None,
        descriptor_lookup: Any = None,
    ) -> None:
        """Register list_tools and call_tool handlers on the Server.

        The call_tool handler extracts the progress token from the MCP
        request context (if present) and passes it to the router via
        the ``extra`` dict so that the router can stream chunks as
        ``notifications/progress`` messages.

        Args:
            server: The MCP Server to register handlers on.
            tools: List of Tool objects to expose via list_tools.
            router: A router with an async handle_call(name, arguments, extra)
                    method that returns (content_list, is_error, trace_id).
            async_bridge: Optional :class:`AsyncTaskBridge`. When present,
                four ``__apcore_task_*`` meta-tools are appended to ``tools``
                and async-hinted modules are routed through the bridge.
            approval_bridge: Optional :class:`ApprovalBridge`. When present,
                ``__apcore_approval_check`` meta-tool is appended to ``tools``
                and approval poll calls are routed through the bridge.
            descriptor_lookup: Optional callable ``(module_id) -> descriptor``
                used by the handler to detect async-hinted modules and feed
                the bridge's submit meta-tool.
        """
        # Meta-tools are surfaced alongside regular tools so MCP clients can
        # discover the submit/status/cancel/list API via list_tools.
        combined_tools: list[mcp_types.Tool] = list(tools)
        if async_bridge is not None:
            combined_tools.extend(async_bridge.build_meta_tools())
        if approval_bridge is not None:
            combined_tools.extend(approval_bridge.build_meta_tools())

        @server.list_tools()
        async def handle_list_tools() -> list[mcp_types.Tool]:
            return list(combined_tools)

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
            from mcp.server.lowlevel.server import request_ctx

            ctx = request_ctx.get()
            progress_token = ctx.meta.progressToken if ctx.meta else None

            # Always pass session for elicitation support
            extra: dict[str, Any] = {"session": ctx.session}

            if ctx.meta is not None:
                meta_dump = ctx.meta.model_dump(exclude_none=True)
                if meta_dump:
                    extra["_meta"] = meta_dump

            # Bridge authenticated identity from ASGI middleware
            identity = auth_identity_var.get()
            if identity is not None:
                extra["identity"] = identity

            if progress_token is not None:

                async def send_notification(notification: dict[str, Any]) -> None:
                    await ctx.session.send_progress_notification(
                        progress_token=notification["params"]["progressToken"],
                        progress=notification["params"]["progress"],
                        total=notification["params"].get("total"),
                        message=notification["params"].get("message"),
                    )

                extra["send_notification"] = send_notification
                extra["progress_token"] = progress_token

            # Meta-tool route: handled entirely by the async bridge.
            if async_bridge is not None and async_bridge.is_meta_tool(name):
                content, is_error, _trace_id = await async_bridge.handle_meta_tool(
                    name,
                    arguments or {},
                    resolve_descriptor=descriptor_lookup,
                    router_extra=extra,
                )
            # Approval meta-tool route: handled by the approval bridge.
            elif approval_bridge is not None and approval_bridge.is_meta_tool(name):
                content, is_error, _trace_id = await approval_bridge.handle_meta_tool(name, arguments or {})
            # Async-hint route: submit to AsyncTaskManager, return task envelope.
            elif (
                async_bridge is not None
                and descriptor_lookup is not None
                and async_bridge.is_async_module(descriptor_lookup(name))
            ):
                from apcore import Context
                from apcore.trace_context import TraceContext, TraceParent

                trace_parent: TraceParent | None = None
                meta_in = extra.get("_meta") if isinstance(extra.get("_meta"), dict) else None
                if meta_in is not None:
                    raw_tp = meta_in.get("traceparent")
                    if isinstance(raw_tp, str):
                        trace_parent = TraceContext.extract({"traceparent": raw_tp})
                submit_ctx = Context.create(data={}, identity=identity, trace_parent=trace_parent)
                try:
                    # [TM-4] Forward the active transport session id so the
                    # bridge can record this task under that session. The
                    # transport sets ``transport_session_var`` in
                    # ``_scoped_session``; on disconnect, the same id is
                    # passed to ``bridge.cancel_session_tasks`` for mass
                    # cancellation. ``None`` is fine — the bridge skips
                    # session indexing when no key is supplied.
                    envelope = await async_bridge.submit(
                        name,
                        arguments or {},
                        submit_ctx,
                        progress_token=extra.get("progress_token"),
                        send_notification=extra.get("send_notification"),
                        session_key=transport_session_var.get(),
                    )
                    import json as _json

                    content = [{"type": "text", "text": _json.dumps(envelope)}]
                    is_error = False
                except Exception as exc:
                    logger.error("async submit failed for %s: %s", name, exc)
                    info = self._error_mapper.to_mcp_error(exc)
                    content = [{"type": "text", "text": info["message"]}]
                    is_error = True
            else:
                content, is_error, _trace_id = await router.handle_call(name, arguments or {}, extra=extra)

            # NOTE: The MCP SDK decorator always wraps our return in
            # CallToolResult(isError=False). Setting isError=True or _meta
            # is not supported by the current SDK decorator. For errors,
            # we raise so the SDK sets isError=True on the CallToolResult.
            # Per-content `meta` (TextContent-level _meta) is allowed and is
            # used to carry W3C `traceparent` back to the client.
            text_contents: list[mcp_types.TextContent] = []
            for item in content:
                if item.get("type") != "text":
                    continue
                item_meta = item.get("_meta")
                if isinstance(item_meta, dict) and item_meta:
                    text_contents.append(mcp_types.TextContent(type="text", text=item["text"], meta=item_meta))
                else:
                    text_contents.append(mcp_types.TextContent(type="text", text=item["text"]))
            if is_error:
                raise Exception(text_contents[0].text if text_contents else "Unknown error")
            return text_contents

    def register_resource_handlers(
        self,
        server: Server,
        registry: Any,
        router: Any | None = None,
    ) -> None:
        """Register list_resources/list_resource_templates/read_resource handlers.

        Two independent sources feed the resource surface:

        - Modules with a non-null ``documentation`` field are exposed as
          ``docs://{module_id}`` resources (unchanged behaviour).
        - [aiperceivable/apcore-mcp#15] Read-only ``system.*`` modules
          (``system.health.*``, ``system.usage.*``, ``system.manifest.*``)
          are exposed under the ``apcore://`` scheme: the three
          summary/full modules as static resources, the three per-module
          ones as resource *templates* parameterized by ``{module_id}``.
          Only module ids the registry actually holds get a resource --
          classification is by module_id prefix alone (see
          :func:`is_readonly_system_module`); there is no separate
          enable/disable toggle for this. Reading any ``apcore://system.*``
          URI dispatches through ``router.handle_call`` -- never directly
          against the registry/module -- so ACL and audit are never
          bypassed for a call made via resources/read instead of
          tools/call.

        Args:
            server: The MCP Server to register handlers on.
            registry: An apcore Registry with list() and get_definition() methods.
            router: An :class:`~apcore_mcp.server.router.ExecutionRouter` (or
                compatible stub) with an async ``handle_call(module_id,
                arguments, extra)`` method. Required for the ``apcore://``
                system-module resources; when ``None`` those resources are
                not registered (the ``docs://`` behaviour is unaffected).
        """
        # Build a map of module_id -> documentation for modules with docs
        docs_map: dict[str, str] = {}
        registered_ids: set[str] = set()
        for module_id in registry.list():
            registered_ids.add(module_id)
            try:
                descriptor = registry.get_definition(module_id)
                if descriptor is not None and getattr(descriptor, "documentation", None):
                    docs_map[module_id] = descriptor.documentation
            except Exception as e:
                logger.warning("Failed to get definition for %s: %s", module_id, e)

        # [aiperceivable/apcore-mcp#15] Only project a system.* resource for a
        # module id the registry actually holds -- registering one for a
        # module that was never registered (e.g. sys_modules disabled, or a
        # future SDK exposing a subset) would advertise a resource that 404s
        # on every read.
        static_system_ids = [mid for mid in _SYSTEM_STATIC_RESOURCE_IDS if mid in registered_ids] if router else []
        template_system_ids = [mid for mid in _SYSTEM_TEMPLATE_RESOURCE_IDS if mid in registered_ids] if router else []

        @server.list_resources()
        async def handle_list_resources() -> list[mcp_types.Resource]:
            resources: list[mcp_types.Resource] = []
            for mid in docs_map:
                resources.append(
                    mcp_types.Resource(
                        uri=AnyUrl(f"docs://{mid}"),
                        name=f"{mid} documentation",
                        mimeType="text/plain",
                    )
                )
            for mid in static_system_ids:
                resources.append(
                    mcp_types.Resource(
                        uri=AnyUrl(f"{_SYSTEM_RESOURCE_SCHEME}://{mid}"),
                        name=mid,
                        mimeType="application/json",
                    )
                )
            return resources

        if template_system_ids:
            # [aiperceivable/apcore-mcp#15] Requires the installed MCP SDK to
            # support resources/templates/list (``Server.list_resource_templates``);
            # this repo's `mcp>=1.26` floor does.
            @server.list_resource_templates()
            async def handle_list_resource_templates() -> list[mcp_types.ResourceTemplate]:
                return [
                    mcp_types.ResourceTemplate(
                        uriTemplate=(
                            f"{_SYSTEM_RESOURCE_SCHEME}://{mid}/{{module_id}}"
                            f"{_SYSTEM_TEMPLATE_QUERY_SUFFIX.get(mid, '')}"
                        ),
                        name=mid,
                        mimeType="application/json",
                    )
                    for mid in template_system_ids
                ]

        @server.read_resource()
        async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
            uri_str = str(uri)
            docs_prefix = "docs://"
            system_prefix = f"{_SYSTEM_RESOURCE_SCHEME}://system."
            if uri_str.startswith(system_prefix):
                return await self._read_system_resource(uri, uri_str, router, static_system_ids, template_system_ids)
            if not uri_str.startswith(docs_prefix):
                raise ValueError(f"Unsupported URI scheme: {uri_str}")
            module_id = uri_str[len(docs_prefix) :]
            if module_id not in docs_map:
                raise ValueError(f"Resource not found: {uri_str}")
            return [ReadResourceContents(content=docs_map[module_id], mime_type="text/plain")]

    async def _read_system_resource(
        self,
        uri: Any,
        uri_str: str,
        router: Any | None,
        static_system_ids: list[str],
        template_system_ids: list[str],
    ) -> list[ReadResourceContents]:
        """Resolve an ``apcore://system.*`` resource read via the router.

        [aiperceivable/apcore-mcp#15] Parses the module id and optional
        ``period`` query parameter out of *uri*, assembles tool-call-style
        arguments, and dispatches through ``router.handle_call`` -- the same
        entry point ``tools/call`` uses -- so ACL and audit apply identically
        regardless of whether the caller used ``tools/call`` or
        ``resources/read``. Errors (unknown resource, ACL denial, execution
        failure) are all raised as :class:`ValueError`, matching the
        ``docs://`` "Resource not found" style already used by this handler.
        """
        if router is None:
            raise ValueError(f"Resource not found: {uri_str}")

        base_module_id = uri.host or ""
        raw_path = (uri.path or "").lstrip("/")
        target_module_id = unquote(raw_path) if raw_path else None
        period = None
        if uri.query:
            parsed_query = parse_qs(uri.query)
            values = parsed_query.get("period")
            if values:
                period = values[0]

        arguments: dict[str, Any] = {}
        if base_module_id in static_system_ids:
            if base_module_id == "system.usage.summary" and period is not None:
                arguments["period"] = period
        elif base_module_id in template_system_ids:
            if not target_module_id:
                raise ValueError(f"Resource not found: {uri_str} (missing module_id path segment)")
            arguments["module_id"] = target_module_id
            if base_module_id == "system.usage.module" and period is not None:
                arguments["period"] = period
        else:
            raise ValueError(f"Resource not found: {uri_str}")

        extra: dict[str, Any] = {}
        identity = auth_identity_var.get()
        if identity is not None:
            extra["identity"] = identity

        content, is_error, _trace_id = await router.handle_call(base_module_id, arguments, extra=extra)
        text_parts = [item["text"] for item in content if item.get("type") == "text"]
        text_output = "\n".join(text_parts) if text_parts else json.dumps({})
        if is_error:
            raise ValueError(f"Resource not found: {uri_str} ({text_output})")
        return [ReadResourceContents(content=text_output, mime_type="application/json")]

    def build_init_options(
        self,
        server: Server,
        name: str,
        version: str,
        *,
        management_surfaces: dict[str, bool] | None = None,
    ) -> InitializationOptions:
        """Build InitializationOptions for running the server.

        Args:
            server: The configured Server instance.
            name: Server name.
            version: Server version.
            management_surfaces: [aiperceivable/apcore-mcp#16 Phase A]
                Optional ``{"health": bool, "usage": bool, "manifest": bool,
                "control": bool}`` -- when any value is true, the
                ``com.aiperceivable/management`` extension capability is
                advertised in ``initialize``'s ``capabilities`` with
                ``surfaces`` listing only the true keys (stable order:
                health, usage, manifest, control). A client that ignores
                this capability keeps working exactly as before -- it is
                purely additive discovery, not a behaviour gate. ``None`` or
                all-false omits the capability entirely, matching pre-#16
                behaviour.

        Returns:
            InitializationOptions ready for server.run().
        """
        capabilities = server.get_capabilities(
            # [A-D-FA-1] resources_changed must be advertised explicitly:
            # NotificationOptions defaults it to False, which surfaced as
            # `resources: {listChanged: false}` in the initialize response
            # and stopped clients from subscribing to
            # notifications/resources/list_changed. TypeScript declares
            # `resources: { listChanged: true }` (factory.ts:131) and Rust
            # returns ResourcesCapability { list_changed: true }
            # (factory.rs:690).
            notification_options=NotificationOptions(tools_changed=True, resources_changed=True),
            experimental_capabilities={},
        )

        if management_surfaces:
            surfaces = [key for key in _MANAGEMENT_SURFACE_ORDER if management_surfaces.get(key)]
            if surfaces:
                # `ServerCapabilities` declares `model_config = ConfigDict(extra="allow")`,
                # so setting an undeclared attribute is a supported way to add a
                # top-level capability key this MCP SDK version has no dedicated
                # field for -- it round-trips through model_dump()/model_dump_json()
                # like any other field.
                capabilities.extensions = {  # type: ignore[attr-defined]
                    _MANAGEMENT_EXTENSION_KEY: {
                        "surfaces": surfaces,
                        "protocolVersion": _PROTOCOL_SPEC_VERSION,
                    }
                }

        return InitializationOptions(
            server_name=name,
            server_version=version,
            capabilities=capabilities,
        )
