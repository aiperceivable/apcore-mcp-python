"""Unit tests for MCPServerFactory."""

from __future__ import annotations

import pytest
from mcp import types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions

from apcore_mcp.server.factory import MCPServerFactory
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
        """build_tool handles empty input schema by producing {type: object, properties: {}}."""
        tool = factory.build_tool(empty_schema_descriptor)
        assert tool.inputSchema == {"type": "object", "properties": {}}

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
            input_schema={
                "$defs": {},
                "properties": {"x": {"$ref": "#/$defs/Missing"}},
            },
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
