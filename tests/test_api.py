"""Tests for the apcore-mcp public API: serve() and to_openai_tools()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apcore_mcp
from apcore_mcp import serve, to_openai_tools
from apcore_mcp._utils import resolve_executor, resolve_registry
from tests.conftest import ModuleAnnotations, ModuleDescriptor

# ---------------------------------------------------------------------------
# Stub Registry / Executor for public API tests
# ---------------------------------------------------------------------------


class StubRegistry:
    """Minimal Registry stub with list() and get_definition()."""

    def __init__(self, descriptors: list[ModuleDescriptor] | None = None):
        self._descriptors = {d.module_id: d for d in (descriptors or [])}

    def list(self, tags=None, prefix=None):
        ids = list(self._descriptors.keys())
        if prefix is not None:
            ids = [mid for mid in ids if mid.startswith(prefix)]
        if tags is not None:
            tag_set = set(tags)
            ids = [mid for mid in ids if tag_set.issubset(set(self._descriptors[mid].tags))]
        return sorted(ids)

    def get_definition(self, module_id):
        return self._descriptors.get(module_id)


class StubExecutor:
    """Minimal Executor stub with call_async() and registry attribute."""

    def __init__(self, registry: StubRegistry):
        self.registry = registry

    async def call_async(self, _module_id: str, _inputs: dict | None = None) -> dict:
        return {"ok": True}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_descriptors() -> list[ModuleDescriptor]:
    return [
        ModuleDescriptor(
            module_id="image.resize",
            description="Resize an image",
            input_schema={
                "type": "object",
                "properties": {
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
                "required": ["width", "height"],
            },
            output_schema={"type": "object"},
            tags=["image"],
            annotations=ModuleAnnotations(idempotent=True),
        ),
        ModuleDescriptor(
            module_id="text.echo",
            description="Echo text",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            output_schema={"type": "object"},
            tags=["text"],
        ),
    ]


@pytest.fixture
def registry(sample_descriptors) -> StubRegistry:
    return StubRegistry(sample_descriptors)


@pytest.fixture
def executor(registry) -> StubExecutor:
    return StubExecutor(registry)


# ===========================================================================
# Tests for resolve_registry
# ===========================================================================


class TestResolveRegistry:
    """Tests for resolve_registry helper."""

    def test_returns_registry_from_executor(self, registry, executor):
        """When given an executor, extracts its .registry attribute."""
        result = resolve_registry(executor)
        assert result is registry

    def test_returns_registry_directly(self, registry):
        """When given a registry (no .registry attr), returns it as-is."""
        result = resolve_registry(registry)
        assert result is registry


# ===========================================================================
# Tests for resolve_executor
# ===========================================================================


class TestResolveExecutor:
    """Tests for resolve_executor helper."""

    def test_returns_executor_directly(self, executor):
        """When given an executor (has .call_async), returns it as-is."""
        result = resolve_executor(executor)
        assert result is executor

    def test_creates_executor_from_registry(self, registry):
        """When given a registry, imports and creates an Executor."""
        import sys

        mock_executor_cls = MagicMock()
        mock_executor_instance = MagicMock()
        mock_executor_cls.return_value = mock_executor_instance

        fake_apcore_executor = MagicMock()
        fake_apcore_executor.Executor = mock_executor_cls
        sys.modules["apcore"] = MagicMock()
        sys.modules["apcore.executor"] = fake_apcore_executor

        try:
            result = resolve_executor(registry)
            mock_executor_cls.assert_called_once_with(registry, approval_handler=None)
            assert result is mock_executor_instance
        finally:
            del sys.modules["apcore.executor"]
            del sys.modules["apcore"]


# ===========================================================================
# Tests for to_openai_tools
# ===========================================================================


class TestToOpenaiTools:
    """Tests for to_openai_tools() public API."""

    def test_basic_conversion_with_registry(self, registry):
        """Converts registry modules to OpenAI tool format."""
        tools = to_openai_tools(registry)
        assert isinstance(tools, list)
        assert len(tools) == 2

        # Check structure of first tool
        tool = tools[0]
        assert tool["type"] == "function"
        assert "function" in tool
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]

    def test_conversion_with_executor(self, executor):
        """Accepts an Executor and extracts its registry for conversion."""
        tools = to_openai_tools(executor)
        assert len(tools) == 2

    def test_tool_names_are_normalized(self, registry):
        """Module IDs with dots are converted to dashes."""
        tools = to_openai_tools(registry)
        names = {t["function"]["name"] for t in tools}
        assert "image-resize" in names
        assert "text-echo" in names

    def test_embed_annotations(self, registry):
        """embed_annotations=True appends annotation info to descriptions."""
        tools = to_openai_tools(registry, embed_annotations=True)
        # Find the tool with annotations
        image_tool = next(t for t in tools if t["function"]["name"] == "image-resize")
        assert "[Annotations:" in image_tool["function"]["description"]

    def test_strict_mode(self, registry):
        """strict=True adds strict: true to function definitions."""
        tools = to_openai_tools(registry, strict=True)
        for tool in tools:
            assert tool["function"]["strict"] is True

    def test_filter_by_tags(self, registry):
        """tags parameter filters modules by tag."""
        tools = to_openai_tools(registry, tags=["image"])
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "image-resize"

    def test_filter_by_prefix(self, registry):
        """prefix parameter filters modules by ID prefix."""
        tools = to_openai_tools(registry, prefix="text")
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "text-echo"

    def test_empty_registry(self):
        """Empty registry returns empty list."""
        empty_reg = StubRegistry([])
        tools = to_openai_tools(empty_reg)
        assert tools == []


# ===========================================================================
# Tests for serve()
# ===========================================================================


class TestServe:
    """Tests for serve() public API."""

    def test_serve_stdio_wires_components(self, registry):
        """serve() with stdio transport wires factory, router, transport correctly."""
        with (
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter"),
        ):
            # Set up factory mock
            mock_factory = mock_factory_cls.return_value
            mock_server = MagicMock()
            mock_factory.create_server.return_value = mock_server
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            # Set up transport mock
            mock_tm = mock_tm_cls.return_value
            mock_tm.run_stdio = AsyncMock()

            serve(registry, transport="stdio", name="test-server", version="1.0.0")

            mock_factory.create_server.assert_called_once_with(name="test-server", version="1.0.0")
            mock_factory.build_tools.assert_called_once_with(registry, tags=None, prefix=None)
            mock_factory.register_handlers.assert_called_once()
            mock_factory.build_init_options.assert_called_once()

    def test_serve_streamable_http(self, registry):
        """serve() with streamable-http transport passes host and port."""
        with (
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter"),
        ):
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = mock_tm_cls.return_value
            mock_tm.run_streamable_http = AsyncMock()

            serve(registry, transport="streamable-http", host="0.0.0.0", port=9000)

            mock_tm.run_streamable_http.assert_called_once()
            call_kwargs = mock_tm.run_streamable_http.call_args
            assert call_kwargs.kwargs["host"] == "0.0.0.0"
            assert call_kwargs.kwargs["port"] == 9000

    def test_serve_sse(self, registry):
        """serve() with sse transport passes host and port."""
        with (
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter"),
        ):
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = mock_tm_cls.return_value
            mock_tm.run_sse = AsyncMock()

            serve(registry, transport="sse", host="localhost", port=8080)

            mock_tm.run_sse.assert_called_once()
            call_kwargs = mock_tm.run_sse.call_args
            assert call_kwargs.kwargs["host"] == "localhost"
            assert call_kwargs.kwargs["port"] == 8080

    def test_serve_unknown_transport(self, registry):
        """serve() with unknown transport raises ValueError."""
        with (
            patch("apcore_mcp.TransportManager"),
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter"),
        ):
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            with pytest.raises(ValueError, match="Unknown transport.*'websocket'"):
                serve(registry, transport="websocket")

    def test_serve_default_version(self, registry):
        """serve() uses __version__ when version is not specified."""
        with (
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter"),
        ):
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = mock_tm_cls.return_value
            mock_tm.run_stdio = AsyncMock()

            serve(registry)

            call_kwargs = mock_factory.create_server.call_args
            assert call_kwargs.kwargs["version"] == apcore_mcp.__version__

    def test_serve_with_executor(self, executor):
        """serve() accepts an Executor and extracts its registry."""
        with (
            patch("apcore_mcp.TransportManager") as mock_tm_cls,
            patch("apcore_mcp.MCPServerFactory") as mock_factory_cls,
            patch("apcore_mcp.ExecutionRouter") as mock_router_cls,
        ):
            mock_factory = mock_factory_cls.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = mock_tm_cls.return_value
            mock_tm.run_stdio = AsyncMock()

            serve(executor)

            # Router should receive the executor with validate_inputs and output_formatter
            mock_router_cls.assert_called_once_with(executor, validate_inputs=False, output_formatter=None)
            # Factory should receive the extracted registry
            mock_factory.build_tools.assert_called_once_with(executor.registry, tags=None, prefix=None)
