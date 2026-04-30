"""MCPServerFactory: create and configure MCP Server with tools from apcore Registry."""

from __future__ import annotations

import logging
from typing import Any

from apcore.schema.exporter import SchemaExporter
from apcore.schema.types import SchemaDefinition
from mcp import types as mcp_types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.schema import SchemaConverter
from apcore_mcp.auth.middleware import auth_identity_var
from apcore_mcp.server.async_task_bridge import RESERVED_PREFIX, AsyncTaskBridge
from apcore_mcp.server.transport import transport_session_var

logger = logging.getLogger(__name__)


_AI_INTENT_KEYS = ("x-when-to-use", "x-when-not-to-use", "x-common-mistakes", "x-workflow-hints")


class MCPServerFactory:
    """Creates and configures MCP Server instances from apcore Registry."""

    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict
        self._schema_converter = SchemaConverter(strict=strict)
        self._annotation_mapper = AnnotationMapper()
        self._schema_exporter = SchemaExporter()
        from apcore_mcp.adapters.errors import ErrorMapper

        self._error_mapper = ErrorMapper()

    def create_server(self, name: str = "apcore-mcp", version: str = "0.1.0") -> Server:
        """Create a new MCP low-level Server instance.

        Args:
            name: Server name for identification.
            version: Server version string. Note: the MCP SDK's ``Server``
                constructor only accepts ``name``; ``version`` is surfaced to
                clients through :meth:`build_init_options` / ``InitializationOptions``.

        Returns:
            A configured Server. Handlers are NOT registered yet.
        """
        return Server(name)

    def build_tool(
        self,
        descriptor: Any,
        *,
        registry: Any | None = None,
        strict: bool = True,
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
            input_schema = self._schema_converter.convert_input_schema(descriptor)

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
        if hints.get("streaming"):
            if meta is None:
                meta = {}
            meta["streaming"] = True

        # Resolve display overlay fields (§5.13)
        metadata = getattr(descriptor, "metadata", None) or {}
        display = metadata.get("display") or {}
        mcp_display = display.get("mcp") or {}

        tool_name: str = mcp_display.get("alias") or descriptor.module_id
        description: str = mcp_display.get("description") or descriptor.description

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
    ) -> list[mcp_types.Tool]:
        """Build Tool objects for all modules in a Registry.

        Uses registry.list(tags=tags, prefix=prefix) to discover module IDs,
        then registry.get_definition() to obtain each descriptor. Modules
        whose definition is None are skipped. Errors during build_tool are
        logged as warnings and the module is skipped.

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
            descriptor = registry.get_definition(module_id)
            if descriptor is None:
                logger.warning("Skipped module %s: no definition found", module_id)
                continue
            try:
                # [A-D-012] Pass the registry so build_tool can prefer
                # registry.export_schema(strict=True) over local conversion.
                tools.append(self.build_tool(descriptor, registry=registry))
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
            descriptor_lookup: Optional callable ``(module_id) -> descriptor``
                used by the handler to detect async-hinted modules and feed
                the bridge's submit meta-tool.
        """
        # Meta-tools are surfaced alongside regular tools so MCP clients can
        # discover the submit/status/cancel/list API via list_tools.
        combined_tools: list[mcp_types.Tool] = list(tools)
        if async_bridge is not None:
            combined_tools.extend(async_bridge.build_meta_tools())

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
    ) -> None:
        """Register list_resources and read_resource handlers for modules with documentation.

        Iterates over registry.list(), gets each definition, and filters for
        descriptors that have a non-null ``documentation`` field. Registers:
        - list_resources: returns Resource objects with URI ``docs://{module_id}``
        - read_resource: returns documentation text for the requested module

        Args:
            server: The MCP Server to register handlers on.
            registry: An apcore Registry with list() and get_definition() methods.
        """
        # Build a map of module_id -> documentation for modules with docs
        docs_map: dict[str, str] = {}
        for module_id in registry.list():
            try:
                descriptor = registry.get_definition(module_id)
                if descriptor is not None and getattr(descriptor, "documentation", None):
                    docs_map[module_id] = descriptor.documentation
            except Exception as e:
                logger.warning("Failed to get definition for %s: %s", module_id, e)

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
            return resources

        @server.read_resource()
        async def handle_read_resource(uri: Any) -> list[ReadResourceContents]:
            uri_str = str(uri)
            prefix = "docs://"
            if not uri_str.startswith(prefix):
                raise ValueError(f"Unsupported URI scheme: {uri_str}")
            module_id = uri_str[len(prefix) :]
            if module_id not in docs_map:
                raise ValueError(f"Resource not found: {uri_str}")
            return [ReadResourceContents(content=docs_map[module_id], mime_type="text/plain")]

    def build_init_options(
        self,
        server: Server,
        name: str,
        version: str,
    ) -> InitializationOptions:
        """Build InitializationOptions for running the server.

        Args:
            server: The configured Server instance.
            name: Server name.
            version: Server version.

        Returns:
            InitializationOptions ready for server.run().
        """
        return InitializationOptions(
            server_name=name,
            server_version=version,
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(tools_changed=True),
                experimental_capabilities={},
            ),
        )
