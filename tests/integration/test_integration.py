"""Integration tests for apcore-mcp.

These tests wire together real components (SchemaConverter, AnnotationMapper,
ErrorMapper, MCPServerFactory, ExecutionRouter, RegistryListener, OpenAIConverter)
and only mock the external boundary (Executor, Registry).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import types as mcp_types

from apcore_mcp import to_openai_tools
from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.listener import RegistryListener
from apcore_mcp.server.router import ExecutionRouter
from tests.conftest import ModuleAnnotations, ModuleDescriptor

# ---------------------------------------------------------------------------
# Stubs: only mock the external boundary
# ---------------------------------------------------------------------------


class StubRegistry:
    """Stub for apcore Registry (no event support)."""

    def __init__(self, descriptors: list[ModuleDescriptor] | None = None) -> None:
        self._descriptors: dict[str, ModuleDescriptor] = {d.module_id: d for d in (descriptors or [])}

    def list(self, tags: list[str] | None = None, prefix: str | None = None) -> list[str]:
        ids = list(self._descriptors.keys())
        if prefix is not None:
            ids = [mid for mid in ids if mid.startswith(prefix)]
        if tags is not None:
            tag_set = set(tags)
            ids = [mid for mid in ids if tag_set.issubset(set(self._descriptors[mid].tags))]
        return sorted(ids)

    def get_definition(self, module_id: str) -> ModuleDescriptor | None:
        return self._descriptors.get(module_id)


class StubExecutor:
    """Stub for apcore Executor."""

    def __init__(
        self,
        results: dict[str, Any] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._results = results or {}
        self._errors = errors or {}

    async def call_async(self, module_id: str, inputs: dict[str, Any] | None = None) -> Any:
        if module_id in self._errors:
            raise self._errors[module_id]
        return self._results.get(module_id, {"ok": True})


class StubACLDeniedError(Exception):
    """Stub for apcore ACLDeniedError with code/message/details attributes."""

    def __init__(self) -> None:
        super().__init__("Access denied")
        self.code = "ACL_DENIED"
        self.message = "Access denied"
        self.details = None


class EventRegistry:
    """Stub Registry with event callback support for RegistryListener tests."""

    def __init__(self) -> None:
        self._callbacks: dict[str, list[Any]] = {}
        self._definitions: dict[str, ModuleDescriptor] = {}

    def on(self, event: str, callback: Any) -> None:
        self._callbacks.setdefault(event, []).append(callback)

    def get_definition(self, module_id: str) -> ModuleDescriptor | None:
        return self._definitions.get(module_id)

    def add_definition(self, descriptor: ModuleDescriptor) -> None:
        self._definitions[descriptor.module_id] = descriptor

    def trigger(self, event: str, module_id: str, module: Any = None) -> None:
        for cb in self._callbacks.get(event, []):
            cb(module_id, module)

    def list(self, tags: list[str] | None = None, prefix: str | None = None) -> list[str]:
        return sorted(self._definitions.keys())


# ---------------------------------------------------------------------------
# TC-INT-001: Full MCP flow -- Registry to tool list to tool call to result
# ---------------------------------------------------------------------------


class TestFullMCPFlow:
    """TC-INT-001: Full MCP flow from registry to tool list to tool call to result.

    Uses real SchemaConverter, AnnotationMapper, ErrorMapper, MCPServerFactory,
    and ExecutionRouter. Only the Executor and Registry are stubbed.
    """

    @pytest.fixture
    def descriptor(self) -> ModuleDescriptor:
        return ModuleDescriptor(
            module_id="image.resize",
            description="Resize an image",
            input_schema={
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "description": "Target width"},
                    "height": {"type": "integer", "description": "Target height"},
                },
                "required": ["width"],
            },
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
            tags=["image"],
            annotations=ModuleAnnotations(idempotent=True),
        )

    @pytest.fixture
    def registry(self, descriptor: ModuleDescriptor) -> StubRegistry:
        return StubRegistry([descriptor])

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    @pytest.fixture
    def executor(self) -> StubExecutor:
        return StubExecutor(results={"image.resize": {"status": "ok"}})

    @pytest.fixture
    def router(self, executor: StubExecutor) -> ExecutionRouter:
        return ExecutionRouter(executor)

    def test_build_tools_returns_one_tool(self, factory: MCPServerFactory, registry: StubRegistry) -> None:
        """Registry with one module produces one MCP Tool."""
        tools = factory.build_tools(registry)
        assert len(tools) == 1

    def test_tool_name_matches_module_id(self, factory: MCPServerFactory, registry: StubRegistry) -> None:
        """The MCP tool name is the module_id."""
        tools = factory.build_tools(registry)
        assert tools[0].name == "image.resize"

    def test_tool_has_correct_schema(self, factory: MCPServerFactory, registry: StubRegistry) -> None:
        """The tool inputSchema preserves the module's input_schema structure."""
        tools = factory.build_tools(registry)
        schema = tools[0].inputSchema
        assert schema["type"] == "object"
        assert "width" in schema["properties"]
        assert "height" in schema["properties"]

    def test_tool_has_correct_annotations(self, factory: MCPServerFactory, registry: StubRegistry) -> None:
        """Annotations are properly mapped from ModuleAnnotations to ToolAnnotations."""
        tools = factory.build_tools(registry)
        assert tools[0].annotations is not None
        assert tools[0].annotations.idempotentHint is True

    async def test_router_returns_success(self, router: ExecutionRouter) -> None:
        """Calling the router with a valid tool name returns success."""
        content, is_error, trace_id = await router.handle_call("image.resize", {"width": 800})
        assert is_error is False
        assert len(content) == 1
        assert content[0]["type"] == "text"

    async def test_router_result_contains_expected_data(self, router: ExecutionRouter) -> None:
        """The router result JSON contains the executor's return value."""
        content, is_error, trace_id = await router.handle_call("image.resize", {"width": 800})
        assert is_error is False
        parsed = json.loads(content[0]["text"])
        assert parsed == {"status": "ok"}

    async def test_full_flow_end_to_end(
        self,
        factory: MCPServerFactory,
        registry: StubRegistry,
        router: ExecutionRouter,
    ) -> None:
        """Full flow: build tools from registry, then call router, assert success."""
        # Build tools
        tools = factory.build_tools(registry)
        assert len(tools) == 1
        assert tools[0].name == "image.resize"

        # Call through router
        content, is_error, trace_id = await router.handle_call("image.resize", {"width": 800})
        assert is_error is False
        parsed = json.loads(content[0]["text"])
        assert parsed == {"status": "ok"}


# ---------------------------------------------------------------------------
# TC-INT-003: Error flow -- MCP client calls non-existent tool
# ---------------------------------------------------------------------------


class TestErrorFlowNonExistentTool:
    """TC-INT-003: Calling a non-existent tool returns an error.

    The ExecutionRouter delegates to the Executor, which raises an error
    for unknown module_ids. The ErrorMapper converts the exception into
    a sanitized error response.
    """

    @pytest.fixture
    def descriptor(self) -> ModuleDescriptor:
        return ModuleDescriptor(
            module_id="image.resize",
            description="Resize an image",
            input_schema={
                "type": "object",
                "properties": {"width": {"type": "integer"}},
            },
            output_schema={},
            tags=["image"],
        )

    @pytest.fixture
    def registry(self, descriptor: ModuleDescriptor) -> StubRegistry:
        return StubRegistry([descriptor])

    async def test_nonexistent_tool_returns_error(self) -> None:
        """Calling a tool that does not exist in the executor returns is_error=True."""

        class StrictExecutor:
            """Executor that only knows about specific modules; raises for unknown."""

            def __init__(self, known: dict[str, Any]) -> None:
                self._known = known

            async def call_async(self, module_id: str, inputs: Any = None) -> Any:
                if module_id not in self._known:
                    raise RuntimeError(f"Module not found: {module_id}")
                return self._known[module_id]

        executor = StrictExecutor(known={"image.resize": {"status": "ok"}})
        router = ExecutionRouter(executor)

        content, is_error, trace_id = await router.handle_call("nonexistent.tool", {"key": "value"})

        assert is_error is True

    async def test_nonexistent_tool_error_has_text_content(self) -> None:
        """The error response contains a text content entry with an error message."""

        class StrictExecutor:
            """Executor that only knows about specific modules; raises for unknown."""

            def __init__(self, known: dict[str, Any]) -> None:
                self._known = known

            async def call_async(self, module_id: str, inputs: Any = None) -> Any:
                if module_id not in self._known:
                    raise RuntimeError(f"Module not found: {module_id}")
                return self._known[module_id]

        executor = StrictExecutor(known={"image.resize": {"status": "ok"}})
        router = ExecutionRouter(executor)

        content, is_error, trace_id = await router.handle_call("nonexistent.tool", {"key": "value"})

        assert is_error is True
        assert len(content) == 1
        assert content[0]["type"] == "text"
        assert isinstance(content[0]["text"], str)
        assert len(content[0]["text"]) > 0

    async def test_nonexistent_tool_with_raising_executor(self) -> None:
        """An executor that raises a generic exception for unknown tools produces sanitized error."""

        class RaisingExecutor:
            async def call_async(self, module_id: str, inputs: Any = None) -> Any:
                raise RuntimeError(f"Module not found: {module_id}")

        router = ExecutionRouter(RaisingExecutor())

        content, is_error, trace_id = await router.handle_call("nonexistent.tool", {"key": "value"})

        assert is_error is True
        assert len(content) == 1
        assert content[0]["type"] == "text"
        # Generic exceptions are sanitized to "Internal error occurred"
        assert "internal error" in content[0]["text"].lower()


# ---------------------------------------------------------------------------
# TC-INT-004: to_openai_tools() roundtrip -- format matches OpenAI spec
# ---------------------------------------------------------------------------


class TestOpenAIToolsRoundtrip:
    """TC-INT-004: to_openai_tools() produces valid OpenAI-compatible tool definitions.

    Uses a registry with 3 modules (simple, nested $ref, empty schemas) and
    validates the output structure matches the OpenAI API spec.
    """

    @pytest.fixture
    def simple_desc(self) -> ModuleDescriptor:
        return ModuleDescriptor(
            module_id="text.summarize",
            description="Summarize text",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "max_length": {"type": "integer"},
                },
                "required": ["text"],
            },
            output_schema={},
            tags=["text"],
        )

    @pytest.fixture
    def nested_desc(self) -> ModuleDescriptor:
        return ModuleDescriptor(
            module_id="workflow.run",
            description="Run a workflow",
            input_schema={
                "type": "object",
                "$defs": {
                    "Step": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["name"],
                    }
                },
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/Step"},
                    },
                },
                "required": ["steps"],
            },
            output_schema={},
            tags=["workflow"],
        )

    @pytest.fixture
    def empty_desc(self) -> ModuleDescriptor:
        return ModuleDescriptor(
            module_id="system.ping",
            description="Health check",
            input_schema={},
            output_schema={},
            tags=["system"],
        )

    @pytest.fixture
    def registry(
        self,
        simple_desc: ModuleDescriptor,
        nested_desc: ModuleDescriptor,
        empty_desc: ModuleDescriptor,
    ) -> StubRegistry:
        return StubRegistry([simple_desc, nested_desc, empty_desc])

    def test_returns_list_of_three(self, registry: StubRegistry) -> None:
        """to_openai_tools returns exactly 3 tool definitions."""
        tools = to_openai_tools(registry)
        assert isinstance(tools, list)
        assert len(tools) == 3

    def test_each_tool_has_correct_top_level_structure(self, registry: StubRegistry) -> None:
        """Each tool dict has {type: 'function', function: {...}}."""
        tools = to_openai_tools(registry)
        for tool in tools:
            assert isinstance(tool, dict)
            assert tool["type"] == "function"
            assert "function" in tool
            assert isinstance(tool["function"], dict)

    def test_each_function_has_required_keys(self, registry: StubRegistry) -> None:
        """Each function dict has name, description, and parameters keys."""
        tools = to_openai_tools(registry)
        required_keys = {"name", "description", "parameters"}
        for tool in tools:
            fn = tool["function"]
            assert required_keys.issubset(
                fn.keys()
            ), f"Missing keys in {fn.get('name', 'unknown')}: {required_keys - fn.keys()}"

    def test_module_ids_are_normalized(self, registry: StubRegistry) -> None:
        """All module IDs are normalized (dots replaced with dashes for OpenAI)."""
        tools = to_openai_tools(registry)
        names = [tool["function"]["name"] for tool in tools]
        for name in names:
            assert "." not in name, f"Name '{name}' still contains dots"

    def test_normalized_names_match_expected(self, registry: StubRegistry) -> None:
        """Normalized names are as expected (dot -> dash)."""
        tools = to_openai_tools(registry)
        names = sorted(tool["function"]["name"] for tool in tools)
        assert names == ["system-ping", "text-summarize", "workflow-run"]

    def test_parameters_are_valid_json_schema(self, registry: StubRegistry) -> None:
        """All parameters are valid JSON Schema objects with type: 'object'."""
        tools = to_openai_tools(registry)
        for tool in tools:
            params = tool["function"]["parameters"]
            assert isinstance(params, dict)
            assert params.get("type") == "object", f"Parameters for {tool['function']['name']} missing type: object"

    def test_nested_ref_schema_is_inlined(self, registry: StubRegistry) -> None:
        """The $ref in the nested schema is resolved and inlined, $defs removed."""
        tools = to_openai_tools(registry)
        workflow_tool = next(t for t in tools if t["function"]["name"] == "workflow-run")
        params = workflow_tool["function"]["parameters"]

        # $defs should be removed
        assert "$defs" not in params

        # The steps array items should have the inlined Step schema
        steps_schema = params["properties"]["steps"]
        assert steps_schema["type"] == "array"
        items = steps_schema["items"]
        assert items["type"] == "object"
        assert "name" in items["properties"]

    def test_empty_schema_gets_default(self, registry: StubRegistry) -> None:
        """Empty input_schema is converted to a normalized object schema.
        Strict mode (default per [SC-10]) also injects additionalProperties:false."""
        tools = to_openai_tools(registry)
        ping_tool = next(t for t in tools if t["function"]["name"] == "system-ping")
        params = ping_tool["function"]["parameters"]
        assert params["type"] == "object"
        assert params["properties"] == {}


# ---------------------------------------------------------------------------
# TC-INT-005: Executor passthrough with ACL enforcement
# ---------------------------------------------------------------------------


class TestACLEnforcement:
    """TC-INT-005: Executor passthrough with ACL enforcement.

    Validates that the ExecutionRouter + ErrorMapper correctly handle
    both successful calls and ACL-denied errors from the Executor.
    """

    @pytest.fixture
    def executor(self) -> StubExecutor:
        return StubExecutor(
            results={"public.tool": {"result": "ok"}},
            errors={"private.tool": StubACLDeniedError()},
        )

    @pytest.fixture
    def router(self, executor: StubExecutor) -> ExecutionRouter:
        return ExecutionRouter(executor)

    async def test_public_tool_succeeds(self, router: ExecutionRouter) -> None:
        """Calling a public tool returns success with the expected result."""
        content, is_error, trace_id = await router.handle_call("public.tool", {})

        assert is_error is False
        parsed = json.loads(content[0]["text"])
        assert parsed == {"result": "ok"}

    async def test_private_tool_returns_error(self, router: ExecutionRouter) -> None:
        """Calling a private tool returns is_error=True."""
        content, is_error, trace_id = await router.handle_call("private.tool", {})

        assert is_error is True

    async def test_private_tool_error_message_is_access_denied(self, router: ExecutionRouter) -> None:
        """The error message for ACL-denied is 'Access denied'."""
        content, is_error, trace_id = await router.handle_call("private.tool", {})

        assert is_error is True
        assert "access denied" in content[0]["text"].lower()

    async def test_both_tools_in_sequence(self, router: ExecutionRouter) -> None:
        """Public succeeds, then private fails -- both in sequence."""
        # Public tool call
        content_pub, is_error_pub, _trace_pub = await router.handle_call("public.tool", {})
        assert is_error_pub is False
        parsed = json.loads(content_pub[0]["text"])
        assert parsed == {"result": "ok"}

        # Private tool call
        content_priv, is_error_priv, _trace_priv = await router.handle_call("private.tool", {})
        assert is_error_priv is True
        assert "access denied" in content_priv[0]["text"].lower()


# ---------------------------------------------------------------------------
# TC-INT-006: Dynamic registration -- add module while components are running
# ---------------------------------------------------------------------------


class TestDynamicRegistration:
    """TC-INT-006: Dynamic registration via RegistryListener.

    Validates that a RegistryListener backed by a real MCPServerFactory
    correctly adds and removes tools in response to registry events.
    """

    @pytest.fixture
    def registry(self) -> EventRegistry:
        return EventRegistry()

    @pytest.fixture
    def factory(self) -> MCPServerFactory:
        return MCPServerFactory()

    @pytest.fixture
    def listener(self, registry: EventRegistry, factory: MCPServerFactory) -> RegistryListener:
        return RegistryListener(registry=registry, factory=factory)

    def test_starts_with_empty_tools(self, listener: RegistryListener) -> None:
        """Before any events, the listener has no tools."""
        listener.start()
        assert listener.tools == {}

    def test_register_event_adds_tool(self, listener: RegistryListener, registry: EventRegistry) -> None:
        """Simulating a 'register' event adds the tool to the listener."""
        descriptor = ModuleDescriptor(
            module_id="new.module",
            description="A dynamically registered module",
            input_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
            },
            output_schema={},
        )
        registry.add_definition(descriptor)
        listener.start()

        # Simulate registry register event
        registry.trigger("register", "new.module")

        tools = listener.tools
        assert "new.module" in tools
        assert isinstance(tools["new.module"], mcp_types.Tool)
        assert tools["new.module"].name == "new.module"

    def test_unregister_event_removes_tool(self, listener: RegistryListener, registry: EventRegistry) -> None:
        """Simulating an 'unregister' event removes the tool from the listener."""
        descriptor = ModuleDescriptor(
            module_id="new.module",
            description="A dynamically registered module",
            input_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
            },
            output_schema={},
        )
        registry.add_definition(descriptor)
        listener.start()

        # Register first
        registry.trigger("register", "new.module")
        assert "new.module" in listener.tools

        # Then unregister
        registry.trigger("unregister", "new.module")
        assert "new.module" not in listener.tools

    def test_full_lifecycle_register_then_unregister(self, listener: RegistryListener, registry: EventRegistry) -> None:
        """Full lifecycle: start empty, register, verify, unregister, verify empty."""
        listener.start()
        assert listener.tools == {}

        # Add module
        descriptor = ModuleDescriptor(
            module_id="new.module",
            description="Dynamically added",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
            output_schema={},
            annotations=ModuleAnnotations(readonly=True),
        )
        registry.add_definition(descriptor)
        registry.trigger("register", "new.module")

        # Verify tool is present with correct properties
        tools = listener.tools
        assert "new.module" in tools
        tool = tools["new.module"]
        assert tool.description == "Dynamically added"
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True

        # Remove module
        registry.trigger("unregister", "new.module")
        assert listener.tools == {}

    def test_multiple_modules_register_and_unregister(
        self, listener: RegistryListener, registry: EventRegistry
    ) -> None:
        """Registering multiple modules and selectively unregistering works."""
        listener.start()

        for i in range(3):
            desc = ModuleDescriptor(
                module_id=f"module.{i}",
                description=f"Module {i}",
                input_schema={"type": "object", "properties": {}},
                output_schema={},
            )
            registry.add_definition(desc)
            registry.trigger("register", f"module.{i}")

        assert len(listener.tools) == 3

        # Unregister one
        registry.trigger("unregister", "module.1")
        tools = listener.tools
        assert len(tools) == 2
        assert "module.0" in tools
        assert "module.1" not in tools
        assert "module.2" in tools
