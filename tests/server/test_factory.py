"""Unit tests for MCPServerFactory."""

from __future__ import annotations

import pytest
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

from apcore_mcp.server.factory import MCPServerFactory, is_readonly_system_module
from tests.conftest import ModuleAnnotations, ModuleDescriptor

# ---------------------------------------------------------------------------
# Stub Registry
# ---------------------------------------------------------------------------


class StubRegistry:
    def __init__(self, descriptors):
        self._descriptors = {d.module_id: d for d in descriptors}

    def list(self, tags=None, prefix=None):
        ids = list(self._descriptors.keys())
        if prefix:
            ids = [mid for mid in ids if mid.startswith(prefix)]
        if tags:
            tag_set = set(tags)
            ids = [mid for mid in ids if tag_set.issubset(set(self._descriptors[mid].tags))]
        return sorted(ids)

    def get_definition(self, module_id):
        return self._descriptors.get(module_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateServer:
    """Tests for MCPServerFactory.create_server."""

    def test_create_server_returns_server_instance(self) -> None:
        """create_server returns a Server instance."""
        factory = MCPServerFactory()
        server = factory.create_server()
        assert isinstance(server, Server)

    def test_create_server_with_custom_name(self) -> None:
        """create_server uses the provided name."""
        factory = MCPServerFactory()
        server = factory.create_server(name="my-server")
        assert server.name == "my-server"

    def test_create_server_rejects_empty_name(self) -> None:
        """[D10-002] create_server must reject empty name (spec: non-empty)."""
        factory = MCPServerFactory()
        with pytest.raises(ValueError, match="non-empty"):
            factory.create_server(name="")

    def test_create_server_rejects_name_exceeding_255_chars(self) -> None:
        """[D10-002] create_server must reject name longer than 255 chars (spec: max 255)."""
        factory = MCPServerFactory()
        with pytest.raises(ValueError, match="255"):
            factory.create_server(name="x" * 256)

    def test_create_server_accepts_name_at_255_char_boundary(self) -> None:
        """[D10-002] create_server accepts exactly 255 chars (inclusive boundary)."""
        factory = MCPServerFactory()
        server = factory.create_server(name="x" * 255)
        assert server.name == "x" * 255


class TestBuildTool:
    """Tests for MCPServerFactory.build_tool."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_build_tool_simple_descriptor(self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor) -> None:
        """build_tool returns a Tool from a simple descriptor."""
        tool = factory.build_tool(simple_descriptor)
        assert isinstance(tool, mcp_types.Tool)

    def test_build_tool_name_is_module_id(self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor) -> None:
        """Tool.name should equal descriptor.module_id."""
        tool = factory.build_tool(simple_descriptor)
        assert tool.name == "image.resize"

    def test_build_tool_description(self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor) -> None:
        """Tool.description should equal descriptor.description."""
        tool = factory.build_tool(simple_descriptor)
        assert tool.description == "Resize an image to the specified dimensions"

    def test_build_tool_input_schema_converted(
        self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor
    ) -> None:
        """Tool.inputSchema is properly converted from descriptor.input_schema."""
        tool = factory.build_tool(simple_descriptor)
        # The SchemaConverter should return a dict with type: object
        assert tool.inputSchema["type"] == "object"
        assert "properties" in tool.inputSchema
        assert "width" in tool.inputSchema["properties"]
        assert "height" in tool.inputSchema["properties"]
        assert "image_path" in tool.inputSchema["properties"]

    def test_build_tool_annotations_mapped(
        self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor
    ) -> None:
        """ToolAnnotations properly mapped from descriptor annotations (camelCase fields)."""
        tool = factory.build_tool(simple_descriptor)
        assert tool.annotations is not None
        assert isinstance(tool.annotations, mcp_types.ToolAnnotations)
        # simple_descriptor has idempotent=True, rest defaults
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is True
        assert tool.annotations.title is None

    def test_build_tool_no_annotations(
        self, factory: MCPServerFactory, no_annotations_descriptor: ModuleDescriptor
    ) -> None:
        """build_tool handles None annotations gracefully."""
        tool = factory.build_tool(no_annotations_descriptor)
        assert tool.annotations is not None
        # When annotations is None, AnnotationMapper returns defaults
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is False
        assert tool.annotations.openWorldHint is True

    def test_build_tool_empty_schema(
        self, factory: MCPServerFactory, empty_schema_descriptor: ModuleDescriptor
    ) -> None:
        """An empty runtime schema accepts arbitrary object properties."""
        tool = factory.build_tool(empty_schema_descriptor)
        assert tool.inputSchema == {
            "type": "object",
            "properties": {},
        }

    def test_build_tool_destructive_annotations(
        self, factory: MCPServerFactory, destructive_descriptor: ModuleDescriptor
    ) -> None:
        """Destructive descriptor annotations are properly mapped."""
        tool = factory.build_tool(destructive_descriptor)
        assert tool.annotations is not None
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.openWorldHint is False


class TestBuildToolDisplayOverlay:
    """Tests for MCPServerFactory.build_tool reading metadata['display']['mcp'] (§5.13)."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def _make_descriptor(self, metadata: dict) -> ModuleDescriptor:
        from tests.conftest import ModuleAnnotations

        return ModuleDescriptor(
            module_id="image.resize",
            description="Raw scanner description",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            annotations=ModuleAnnotations(),
            metadata=metadata,
        )

    def test_tool_name_from_mcp_alias(self, factory: MCPServerFactory) -> None:
        """Tool.name is taken from metadata['display']['mcp']['alias'] when set."""
        d = self._make_descriptor({"display": {"mcp": {"alias": "img_resize"}, "alias": "img_resize"}})
        tool = factory.build_tool(d)
        assert tool.name == "img_resize"

    def test_tool_name_falls_back_to_module_id(self, factory: MCPServerFactory) -> None:
        """Tool.name falls back to descriptor.module_id when no MCP alias set."""
        d = self._make_descriptor({})
        tool = factory.build_tool(d)
        assert tool.name == "image.resize"

    def test_tool_description_from_mcp_display(self, factory: MCPServerFactory) -> None:
        """Tool.description is taken from metadata['display']['mcp']['description'] when set."""
        d = self._make_descriptor({"display": {"mcp": {"alias": "img_resize", "description": "MCP-specific desc"}}})
        tool = factory.build_tool(d)
        assert tool.description is not None
        assert "MCP-specific desc" in tool.description

    def test_tool_description_falls_back_to_descriptor(self, factory: MCPServerFactory) -> None:
        """Tool.description falls back to descriptor.description when no MCP description set."""
        d = self._make_descriptor({})
        tool = factory.build_tool(d)
        assert tool.description == "Raw scanner description"

    def test_guidance_appended_to_description(self, factory: MCPServerFactory) -> None:
        """Guidance from display overlay is appended to Tool.description."""
        d = self._make_descriptor(
            {
                "display": {
                    "mcp": {
                        "alias": "img_resize",
                        "description": "Resize image",
                        "guidance": "Use width/height in pixels.",
                    }
                }
            }
        )
        tool = factory.build_tool(d)
        assert tool.description is not None
        assert "Guidance: Use width/height in pixels." in tool.description

    def test_no_guidance_when_not_set(self, factory: MCPServerFactory) -> None:
        """Guidance section is absent when display.mcp.guidance is not set."""
        d = self._make_descriptor({"display": {"mcp": {"alias": "img_resize"}}})
        tool = factory.build_tool(d)
        assert tool.description is not None
        assert "Guidance:" not in tool.description


class TestBuildTools:
    """Tests for MCPServerFactory.build_tools."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_build_tools_multiple(
        self,
        factory: MCPServerFactory,
        simple_descriptor: ModuleDescriptor,
        empty_schema_descriptor: ModuleDescriptor,
        destructive_descriptor: ModuleDescriptor,
    ) -> None:
        """build_tools returns Tool objects for all modules in registry."""
        registry = StubRegistry([simple_descriptor, empty_schema_descriptor, destructive_descriptor])
        tools = factory.build_tools(registry)
        assert len(tools) == 3
        names = {t.name for t in tools}
        assert names == {"image.resize", "system.ping", "file.delete"}

    def test_build_tools_empty_registry(self, factory: MCPServerFactory) -> None:
        """build_tools returns empty list for empty registry."""
        registry = StubRegistry([])
        tools = factory.build_tools(registry)
        assert tools == []

    def test_build_tools_skips_none_definition(
        self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor
    ) -> None:
        """build_tools skips modules where get_definition returns None."""
        registry = StubRegistry([simple_descriptor])
        # Override get_definition to return None for the module
        original_get = registry.get_definition

        def patched_get(module_id):
            if module_id == "image.resize":
                return None
            return original_get(module_id)

        registry.get_definition = patched_get
        tools = factory.build_tools(registry)
        assert tools == []

    def test_build_tools_with_prefix_filter(
        self,
        factory: MCPServerFactory,
        simple_descriptor: ModuleDescriptor,
        empty_schema_descriptor: ModuleDescriptor,
    ) -> None:
        """build_tools passes prefix filter to registry."""
        registry = StubRegistry([simple_descriptor, empty_schema_descriptor])
        tools = factory.build_tools(registry, prefix="image.")
        assert len(tools) == 1
        assert tools[0].name == "image.resize"

    def test_build_tools_with_tags_filter(
        self,
        factory: MCPServerFactory,
        simple_descriptor: ModuleDescriptor,
        empty_schema_descriptor: ModuleDescriptor,
    ) -> None:
        """build_tools passes tags filter to registry."""
        registry = StubRegistry([simple_descriptor, empty_schema_descriptor])
        tools = factory.build_tools(registry, tags=["image"])
        assert len(tools) == 1
        assert tools[0].name == "image.resize"

    def test_build_tools_skips_on_error(self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor) -> None:
        """build_tools catches errors per-module and skips that module."""
        # Create a descriptor that will cause build_tool to raise
        bad_descriptor = ModuleDescriptor(
            module_id="bad.module",
            description="will fail",
            input_schema=["invalid schema"],
            output_schema={},
        )
        registry = StubRegistry([simple_descriptor, bad_descriptor])
        tools = factory.build_tools(registry)
        # The bad module should be skipped, only simple_descriptor built
        assert len(tools) == 1
        assert tools[0].name == "image.resize"


class TestRegisterHandlers:
    """Tests for MCPServerFactory.register_handlers."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_register_handlers(self, factory: MCPServerFactory) -> None:
        """register_handlers registers list_tools and call_tool handlers on the server."""
        server = factory.create_server()
        tools = [
            mcp_types.Tool(
                name="test.tool",
                description="A test tool",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

        class StubRouter:
            async def handle_call(self, name, arguments):
                return [mcp_types.TextContent(type="text", text="ok")], False

        factory.register_handlers(server, tools, StubRouter())
        # The MCP SDK registers handlers keyed by request type classes
        assert mcp_types.ListToolsRequest in server.request_handlers
        assert mcp_types.CallToolRequest in server.request_handlers

    async def test_list_tools_handler_returns_tools(self, factory: MCPServerFactory) -> None:
        """The registered list_tools handler returns the provided tools."""
        server = factory.create_server()
        tool = mcp_types.Tool(
            name="test.tool",
            description="A test tool",
            inputSchema={"type": "object", "properties": {}},
        )

        class StubRouter:
            async def handle_call(self, name, arguments):
                return [mcp_types.TextContent(type="text", text="ok")], False

        factory.register_handlers(server, [tool], StubRouter())
        # Get the handler and call it via its request type key
        handler = server.request_handlers[mcp_types.ListToolsRequest]
        server_result = await handler(None)
        # The SDK wraps the ListToolsResult in a ServerResult envelope
        result = server_result.root
        assert len(result.tools) == 1
        assert result.tools[0].name == "test.tool"


class TestBuildToolAIIntentMetadata:
    """Tests for AI intent metadata in tool descriptions."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_when_to_use_in_description(self, factory: MCPServerFactory) -> None:
        """Tool built with metadata containing x-when-to-use includes it in description."""
        descriptor = ModuleDescriptor(
            module_id="ai.tool",
            description="An AI tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            metadata={"x-when-to-use": "Use when the user needs image generation"},
        )
        tool = factory.build_tool(descriptor)
        assert "When To Use: Use when the user needs image generation" in tool.description

    def test_multiple_intent_keys_in_description(self, factory: MCPServerFactory) -> None:
        """Tool built with multiple intent keys includes all in description."""
        descriptor = ModuleDescriptor(
            module_id="ai.tool",
            description="An AI tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            metadata={
                "x-when-to-use": "Use for image generation",
                "x-common-mistakes": "Do not pass raw bytes",
                "x-workflow-hints": "Call after preprocessing",
            },
        )
        tool = factory.build_tool(descriptor)
        assert "When To Use: Use for image generation" in tool.description
        assert "Common Mistakes: Do not pass raw bytes" in tool.description
        assert "Workflow Hints: Call after preprocessing" in tool.description

    def test_empty_metadata_unchanged_description(self, factory: MCPServerFactory) -> None:
        """Tool built with empty metadata has unchanged description."""
        descriptor = ModuleDescriptor(
            module_id="plain.tool",
            description="A plain tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            metadata={},
        )
        tool = factory.build_tool(descriptor)
        assert tool.description == "A plain tool"

    def test_non_intent_metadata_excluded(self, factory: MCPServerFactory) -> None:
        """Non-intent metadata keys are NOT included in description."""
        descriptor = ModuleDescriptor(
            module_id="custom.tool",
            description="A custom tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            metadata={"custom-key": "some value", "x-internal": "hidden"},
        )
        tool = factory.build_tool(descriptor)
        assert tool.description == "A custom tool"


class TestBuildToolStreamingMeta:
    """Tests for build_tool with streaming annotations."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_build_tool_with_requires_approval_meta(
        self, factory: MCPServerFactory, destructive_descriptor: ModuleDescriptor
    ) -> None:
        """build_tool includes _meta.requiresApproval when annotation is set."""
        tool = factory.build_tool(destructive_descriptor)
        # destructive_descriptor has requires_approval=True
        assert tool.meta is not None
        assert tool.meta.get("requiresApproval") is True

    def test_build_tool_with_streaming_meta(self, factory: MCPServerFactory) -> None:
        """build_tool includes _meta.streaming when annotation has streaming=True."""
        descriptor = ModuleDescriptor(
            module_id="stream.tool",
            description="Streaming tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            annotations=ModuleAnnotations(streaming=True),
        )

        tool = factory.build_tool(descriptor)
        assert tool.meta is not None
        assert tool.meta.get("streaming") is True

    def test_build_tool_with_both_approval_and_streaming(self, factory: MCPServerFactory) -> None:
        """build_tool includes both requires_approval and streaming in _meta."""
        descriptor = ModuleDescriptor(
            module_id="full.tool",
            description="Full tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
            annotations=ModuleAnnotations(destructive=True, requires_approval=True, open_world=False, streaming=True),
        )

        tool = factory.build_tool(descriptor)
        assert tool.meta is not None
        assert tool.meta.get("requiresApproval") is True
        assert tool.meta.get("streaming") is True

    def test_build_tool_no_meta_when_no_special_annotations(
        self, factory: MCPServerFactory, simple_descriptor: ModuleDescriptor
    ) -> None:
        """build_tool has no _meta when there's no requires_approval or streaming."""
        tool = factory.build_tool(simple_descriptor)
        # simple_descriptor has idempotent=True but NOT requires_approval or streaming
        assert tool.meta is None


class TestRegisterResourceHandlers:
    """Tests for MCPServerFactory.register_resource_handlers."""

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_registers_resource_handlers_on_server(self, factory: MCPServerFactory) -> None:
        """register_resource_handlers registers list_resources and read_resource handlers."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.documented",
                    description="A documented module",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    documentation="Some docs",
                )
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        assert mcp_types.ListResourcesRequest in server.request_handlers
        assert mcp_types.ReadResourceRequest in server.request_handlers

    async def test_resource_list_includes_documented_modules(self, factory: MCPServerFactory) -> None:
        """Modules with non-null documentation appear in resource list."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.documented",
                    description="A documented module",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    documentation="Some docs",
                )
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        handler = server.request_handlers[mcp_types.ListResourcesRequest]
        server_result = await handler(None)
        result = server_result.root
        assert len(result.resources) == 1
        resource = result.resources[0]
        assert str(resource.uri) == "docs://mod.documented"
        assert resource.name == "mod.documented documentation"
        assert resource.mimeType == "text/plain"

    async def test_resource_list_excludes_null_documentation(self, factory: MCPServerFactory) -> None:
        """Modules with documentation=None are excluded from resource list."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.nodocs",
                    description="No docs module",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    documentation=None,
                )
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        handler = server.request_handlers[mcp_types.ListResourcesRequest]
        server_result = await handler(None)
        result = server_result.root
        assert len(result.resources) == 0

    async def test_resource_list_excludes_modules_without_documentation(self, factory: MCPServerFactory) -> None:
        """Modules without a documentation field are excluded from resource list."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.plain",
                    description="Plain module",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                )
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        handler = server.request_handlers[mcp_types.ListResourcesRequest]
        server_result = await handler(None)
        result = server_result.root
        assert len(result.resources) == 0

    async def test_resource_read_returns_documentation_text(self, factory: MCPServerFactory) -> None:
        """Reading a valid docs:// URI returns the documentation text."""
        from pydantic import AnyUrl

        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.documented",
                    description="A documented module",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    documentation="Some docs about this module",
                )
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(
                uri=AnyUrl("docs://mod.documented"),
            ),
        )
        server_result = await handler(request)
        result = server_result.root
        assert len(result.contents) == 1
        assert result.contents[0].text == "Some docs about this module"
        assert result.contents[0].mimeType == "text/plain"

    async def test_resource_read_unknown_module_raises_error(self, factory: MCPServerFactory) -> None:
        """Reading a docs:// URI for an unknown module raises an error."""
        from pydantic import AnyUrl

        registry = StubRegistry([])
        server = factory.create_server()
        factory.register_resource_handlers(server, registry)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(
                uri=AnyUrl("docs://mod.unknown"),
            ),
        )
        with pytest.raises(ValueError, match="Resource not found"):
            await handler(request)


class TestBuildInitOptions:
    """Tests for MCPServerFactory.build_init_options."""

    def test_build_init_options(self) -> None:
        """build_init_options creates valid InitializationOptions."""
        factory = MCPServerFactory()
        server = factory.create_server(name="test-server", version="1.2.3")
        # Register dummy handlers so capabilities work
        factory.register_handlers(server, [], type("R", (), {"handle_call": None})())

        opts = factory.build_init_options(server, name="test-server", version="1.2.3")
        assert isinstance(opts, InitializationOptions)
        assert opts.server_name == "test-server"
        assert opts.server_version == "1.2.3"
        assert opts.capabilities is not None

    def test_resources_list_changed_is_advertised(self) -> None:
        """[A-D-FA-1] resources.listChanged must be true, matching TypeScript and Rust.

        NotificationOptions defaults resources_changed to False, which surfaced
        in the initialize response as resources: {listChanged: false} and kept
        clients from ever subscribing to notifications/resources/list_changed.
        """
        factory = MCPServerFactory()
        server = factory.create_server(name="test-server", version="1.2.3")
        factory.register_handlers(server, [], type("R", (), {"handle_call": None})())
        factory.register_resource_handlers(server, StubRegistry([]))

        opts = factory.build_init_options(server, name="test-server", version="1.2.3")

        assert opts.capabilities.resources is not None
        assert opts.capabilities.resources.listChanged is True


class TestBuildInitOptionsManagementExtension:
    """[aiperceivable/apcore-mcp#16 Phase A] `com.aiperceivable/management` capability."""

    def _opts(self, management_surfaces=None):
        factory = MCPServerFactory()
        server = factory.create_server(name="test-server", version="1.2.3")
        factory.register_handlers(server, [], type("R", (), {"handle_call": None})())
        return factory.build_init_options(
            server, name="test-server", version="1.2.3", management_surfaces=management_surfaces
        )

    def test_no_management_surfaces_omits_extension(self) -> None:
        opts = self._opts(None)
        assert (
            not hasattr(opts.capabilities, "extensions")
            or opts.capabilities.model_dump(exclude_none=True).get("extensions") is None
        )

    def test_all_false_omits_extension(self) -> None:
        opts = self._opts({"health": False, "usage": False, "manifest": False, "control": False})
        dumped = opts.capabilities.model_dump(exclude_none=True)
        assert "extensions" not in dumped

    def test_single_true_surface_is_advertised(self) -> None:
        opts = self._opts({"health": True, "usage": False, "manifest": False, "control": False})
        dumped = opts.capabilities.model_dump(exclude_none=True)
        ext = dumped["extensions"]["com.aiperceivable/management"]
        assert ext["surfaces"] == ["health"]
        assert ext["protocolVersion"]

    def test_surfaces_are_emitted_in_stable_order(self) -> None:
        # Pass keys out of order -- output order must not depend on input order.
        opts = self._opts({"control": True, "health": True, "manifest": True, "usage": True})
        dumped = opts.capabilities.model_dump(exclude_none=True)
        ext = dumped["extensions"]["com.aiperceivable/management"]
        assert ext["surfaces"] == ["health", "usage", "manifest", "control"]

    def test_partial_surfaces_only_true_ones_listed(self) -> None:
        opts = self._opts({"health": True, "usage": True, "manifest": False, "control": False})
        dumped = opts.capabilities.model_dump(exclude_none=True)
        ext = dumped["extensions"]["com.aiperceivable/management"]
        assert ext["surfaces"] == ["health", "usage"]

    def test_client_ignoring_extension_still_sees_all_other_capabilities(self) -> None:
        """A client that does not understand the extension still gets a normal,
        fully-functional initialize response -- this is purely additive discovery."""
        opts_with = self._opts({"health": True})
        opts_without = self._opts(None)
        assert opts_with.capabilities.tools == opts_without.capabilities.tools
        assert opts_with.server_name == opts_without.server_name


class TestFactoryInputContract:
    """Legacy strict arguments cannot alter the runtime input contract."""

    @pytest.mark.parametrize("factory_strict", [False, True])
    @pytest.mark.parametrize("strict", [None, False, True])
    def test_preserves_runtime_schema(self, simple_descriptor, factory_strict, strict) -> None:
        factory = MCPServerFactory(strict=factory_strict)
        tool = factory.build_tool(simple_descriptor, strict=strict)
        assert tool.inputSchema == simple_descriptor.input_schema


class TestRichDescription:
    """Tests for MCPServerFactory rich_description=True (apcore-toolkit 0.6+)."""

    def test_rich_description_renders_markdown(self, simple_descriptor):
        plain_factory = MCPServerFactory()
        rich_factory = MCPServerFactory(rich_description=True)
        plain = plain_factory.build_tool(simple_descriptor).description
        rich = rich_factory.build_tool(simple_descriptor).description
        assert rich != plain
        assert rich is not None
        assert rich.startswith("# ")
        # The original short description is embedded within the Markdown body.
        assert simple_descriptor.description in rich

    def test_rich_description_respects_display_overlay_override(self, simple_descriptor):
        """Display-overlay mcp.description is a hard operator override and wins."""
        simple_descriptor.metadata["display"] = {
            "mcp": {"description": "Operator-typed override — keep verbatim"},
        }
        try:
            factory = MCPServerFactory(rich_description=True)
            tool = factory.build_tool(simple_descriptor)
            assert tool.description == "Operator-typed override — keep verbatim"
        finally:
            simple_descriptor.metadata.pop("display", None)

    def test_rich_description_falls_back_when_toolkit_missing(
        self,
        simple_descriptor,
        monkeypatch,
    ):
        from apcore_mcp.server import factory as factory_mod

        monkeypatch.setattr(factory_mod._markdown, "is_available", lambda: False)
        factory = MCPServerFactory(rich_description=True)
        tool = factory.build_tool(simple_descriptor)
        assert tool.description == simple_descriptor.description


# ---------------------------------------------------------------------------
# aiperceivable/apcore-mcp#15 — system.* readonly modules are resources, not tools
# ---------------------------------------------------------------------------


class TestIsReadonlySystemModule:
    """Tests for the module_id-prefix classifier."""

    @pytest.mark.parametrize(
        "module_id",
        [
            "system.health.summary",
            "system.health.module",
            "system.usage.summary",
            "system.usage.module",
            "system.manifest.full",
            "system.manifest.module",
        ],
    )
    def test_readonly_prefixes_are_classified_readonly(self, module_id: str) -> None:
        assert is_readonly_system_module(module_id) is True

    @pytest.mark.parametrize(
        "module_id",
        [
            "system.control.update_config",
            "system.control.reload_module",
            "system.control.toggle_feature",
            "image.resize",
            "system.ping",
        ],
    )
    def test_write_and_non_system_modules_are_not_readonly(self, module_id: str) -> None:
        assert is_readonly_system_module(module_id) is False

    @pytest.mark.parametrize(
        "module_id",
        [
            # A seventh read-only module a future apcore might add, and one a
            # host could register through `register_internal` itself.
            "system.health.history",
            "system.usage.trend",
            "system.manifest.diff",
        ],
    )
    def test_unrecognised_readonly_system_module_stays_a_tool(self, module_id: str) -> None:
        """An id this adapter has no resource for MUST NOT be dropped from tools.

        `register_resource_handlers` builds resources from the six canonical
        ids, so a bare prefix match here would remove such a module from
        `tools/list` while giving it no resource either — it would disappear
        from both discovery surfaces at once. Classifying it as a tool is the
        safer failure: visible and callable until this adapter learns its
        resource shape.
        """
        assert is_readonly_system_module(module_id) is False


class StubRouter:
    """A minimal router stub recording every dispatched call."""

    def __init__(self, *, error: bool = False, error_text: str = "error") -> None:
        self.calls: list[tuple[str, dict]] = []
        self._error = error
        self._error_text = error_text

    async def handle_call(self, name, arguments, extra=None):
        self.calls.append((name, dict(arguments)))
        if self._error:
            return ([{"type": "text", "text": self._error_text}], True, None)
        return ([{"type": "text", "text": '{"ok": true}'}], False, None)


def _system_registry() -> StubRegistry:
    return StubRegistry(
        [
            ModuleDescriptor(module_id="system.health.summary", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(module_id="system.health.module", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(module_id="system.usage.summary", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(module_id="system.usage.module", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(module_id="system.manifest.full", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(module_id="system.manifest.module", description="d", input_schema={}, output_schema={}),
            ModuleDescriptor(
                module_id="system.control.reload_module", description="d", input_schema={}, output_schema={}
            ),
            ModuleDescriptor(module_id="regular.tool", description="d", input_schema={}, output_schema={}),
        ]
    )


class TestBuildToolsExcludesReadonlySystemModules:
    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    def test_readonly_system_modules_are_not_tools(self, factory: MCPServerFactory) -> None:
        tools = factory.build_tools(_system_registry())
        names = {t.name for t in tools}
        assert names == {"system.control.reload_module", "regular.tool"}


class TestRegisterResourceHandlersSystemModules:
    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    async def test_static_system_resources_listed(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        factory.register_resource_handlers(server, _system_registry(), StubRouter())

        handler = server.request_handlers[mcp_types.ListResourcesRequest]
        result = (await handler(None)).root
        uris = {str(r.uri) for r in result.resources}
        assert "apcore://system.health.summary" in uris
        assert "apcore://system.usage.summary" in uris
        assert "apcore://system.manifest.full" in uris
        # Module-scoped (template) ids and the write module must NOT appear here.
        assert "apcore://system.health.module" not in uris
        assert not any("system.control" in u for u in uris)

    async def test_module_scoped_system_resources_listed_as_templates(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        factory.register_resource_handlers(server, _system_registry(), StubRouter())

        handler = server.request_handlers[mcp_types.ListResourceTemplatesRequest]
        result = (await handler(None)).root
        templates = {t.uriTemplate for t in result.resourceTemplates}
        assert "apcore://system.health.module/{module_id}" in templates
        # [aiperceivable/apcore-mcp#15] only `system.usage.module` takes the
        # optional `?period=` query param; cross-checked against
        # apcore-mcp-typescript's `systemResourceUriTemplate()`, which emits
        # the same `{?period}` suffix for this module and none of the others.
        assert "apcore://system.usage.module/{module_id}{?period}" in templates
        assert "apcore://system.manifest.module/{module_id}" in templates

    async def test_read_static_summary_resource_dispatches_through_router(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        router = StubRouter()
        factory.register_resource_handlers(server, _system_registry(), router)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("apcore://system.health.summary")),
        )
        result = (await handler(request)).root
        assert result.contents[0].text == '{"ok": true}'
        assert result.contents[0].mimeType == "application/json"
        assert router.calls == [("system.health.summary", {})]

    async def test_read_usage_summary_forwards_period_query_param(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        router = StubRouter()
        factory.register_resource_handlers(server, _system_registry(), router)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("apcore://system.usage.summary?period=7d")),
        )
        await handler(request)
        assert router.calls == [("system.usage.summary", {"period": "7d"})]

    async def test_read_module_scoped_resource_extracts_module_id(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        router = StubRouter()
        factory.register_resource_handlers(server, _system_registry(), router)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(
                uri=AnyUrl("apcore://system.health.module/orders.create"),
            ),
        )
        await handler(request)
        assert router.calls == [("system.health.module", {"module_id": "orders.create"})]

    async def test_read_usage_module_forwards_module_id_and_period(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        router = StubRouter()
        factory.register_resource_handlers(server, _system_registry(), router)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(
                uri=AnyUrl("apcore://system.usage.module/orders.create?period=1h"),
            ),
        )
        await handler(request)
        assert router.calls == [("system.usage.module", {"module_id": "orders.create", "period": "1h"})]

    async def test_read_module_scoped_resource_missing_module_id_raises(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        factory.register_resource_handlers(server, _system_registry(), StubRouter())

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("apcore://system.health.module")),
        )
        with pytest.raises(ValueError, match="Resource not found"):
            await handler(request)

    async def test_read_unknown_system_resource_raises(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        factory.register_resource_handlers(server, _system_registry(), StubRouter())

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("apcore://system.nonexistent.thing")),
        )
        with pytest.raises(ValueError, match="Resource not found"):
            await handler(request)

    async def test_router_error_surfaces_as_resource_not_found_style_error(self, factory: MCPServerFactory) -> None:
        server = factory.create_server()
        router = StubRouter(error=True, error_text="Access denied: @external -> system.health.summary")
        factory.register_resource_handlers(server, _system_registry(), router)

        handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("apcore://system.health.summary")),
        )
        with pytest.raises(ValueError, match="Access denied"):
            await handler(request)

    async def test_no_router_means_no_system_resources_registered(self, factory: MCPServerFactory) -> None:
        """Without a router, system.* resources are skipped entirely (docs:// unaffected)."""
        server = factory.create_server()
        factory.register_resource_handlers(server, _system_registry(), None)

        list_handler = server.request_handlers[mcp_types.ListResourcesRequest]
        result = (await list_handler(None)).root
        assert result.resources == []
        assert mcp_types.ListResourceTemplatesRequest not in server.request_handlers

    async def test_readonly_module_absent_from_registry_gets_no_resource(self, factory: MCPServerFactory) -> None:
        """Only module ids the registry actually holds get a resource (no aspirational entries)."""
        registry = StubRegistry(
            [
                ModuleDescriptor(module_id="system.health.summary", description="d", input_schema={}, output_schema={}),
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry, StubRouter())

        list_handler = server.request_handlers[mcp_types.ListResourcesRequest]
        result = (await list_handler(None)).root
        uris = {str(r.uri) for r in result.resources}
        assert uris == {"apcore://system.health.summary"}
        # No manifest/usage modules registered -> no templates handler at all.
        assert mcp_types.ListResourceTemplatesRequest not in server.request_handlers

    async def test_docs_resources_unaffected_by_system_resource_support(self, factory: MCPServerFactory) -> None:
        """Existing docs:// behaviour keeps working alongside the new apcore:// resources."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.documented",
                    description="A documented module",
                    input_schema={},
                    output_schema={},
                    documentation="Some docs",
                ),
                ModuleDescriptor(module_id="system.health.summary", description="d", input_schema={}, output_schema={}),
            ]
        )
        server = factory.create_server()
        factory.register_resource_handlers(server, registry, StubRouter())

        list_handler = server.request_handlers[mcp_types.ListResourcesRequest]
        result = (await list_handler(None)).root
        uris = {str(r.uri) for r in result.resources}
        assert "docs://mod.documented" in uris
        assert "apcore://system.health.summary" in uris

        read_handler = server.request_handlers[mcp_types.ReadResourceRequest]
        request = mcp_types.ReadResourceRequest(
            params=mcp_types.ReadResourceRequestParams(uri=AnyUrl("docs://mod.documented")),
        )
        result2 = (await read_handler(request)).root
        assert result2.contents[0].text == "Some docs"
