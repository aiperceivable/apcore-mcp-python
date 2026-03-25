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

logger = logging.getLogger(__name__)


_AI_INTENT_KEYS = ("x-when-to-use", "x-when-not-to-use", "x-common-mistakes", "x-workflow-hints")


class MCPServerFactory:
    """Creates and configures MCP Server instances from apcore Registry."""

    def __init__(self) -> None:
        self._schema_converter = SchemaConverter()
        self._annotation_mapper = AnnotationMapper()
        self._schema_exporter = SchemaExporter()

    def create_server(self, name: str = "apcore-mcp", version: str = "0.1.0") -> Server:
        """Create a new MCP low-level Server instance.

        Args:
            name: Server name for identification.
            version: Server version string.

        Returns:
            A configured Server. Handlers are NOT registered yet.
        """
        return Server(name)

    def build_tool(self, descriptor: Any) -> mcp_types.Tool:
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
                tools.append(self.build_tool(descriptor))
            except Exception as e:
                logger.warning("Failed to build tool for %s: %s", module_id, e)
                continue
        return tools

    def register_handlers(
        self,
        server: Server,
        tools: list[mcp_types.Tool],
        router: Any,
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
        """

        @server.list_tools()
        async def handle_list_tools() -> list[mcp_types.Tool]:
            return list(tools)

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
            from mcp.server.lowlevel.server import request_ctx

            ctx = request_ctx.get()
            progress_token = ctx.meta.progressToken if ctx.meta else None

            # Always pass session for elicitation support
            extra: dict[str, Any] = {"session": ctx.session}

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

            content, is_error, _trace_id = await router.handle_call(name, arguments or {}, extra=extra)

            # NOTE: The MCP SDK decorator always wraps our return in
            # CallToolResult(isError=False). Setting isError=True or _meta
            # is not supported by the current SDK decorator. For errors,
            # we raise so the SDK sets isError=True on the CallToolResult.
            text_contents = [
                mcp_types.TextContent(type="text", text=item["text"]) for item in content if item.get("type") == "text"
            ]
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
