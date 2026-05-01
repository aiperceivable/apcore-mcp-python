"""Unit tests for APCoreMCP unified entry point."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apcore_mcp.apcore_mcp import APCoreMCP

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubDescriptor:
    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.description = f"Stub {module_id}"
        self.input_schema = {"type": "object", "properties": {}}
        self.output_schema = {"type": "object", "properties": {}}
        self.annotations = {}
        self.metadata = {}
        self.documentation = None


class StubRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, StubDescriptor] = {
            "test.hello": StubDescriptor("test.hello"),
            "test.world": StubDescriptor("test.world"),
        }

    def list(self, tags: list[str] | None = None, prefix: str | None = None) -> list[str]:
        ids = list(self._modules.keys())
        if prefix:
            ids = [m for m in ids if m.startswith(prefix)]
        return ids

    def get_definition(self, module_id: str) -> Any:
        return self._modules.get(module_id)

    def discover(self) -> int:
        return len(self._modules)


class StubExecutor:
    def __init__(self, registry: StubRegistry | None = None) -> None:
        self.registry = registry or StubRegistry()

    async def call_async(self, module_id: str, inputs: dict[str, Any]) -> Any:
        return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers to build common patches for serve/async_serve
# ---------------------------------------------------------------------------


def _make_serve_patches():
    """Return patches for the module-level serve() function called by APCoreMCP.serve().

    After the D9-001 refactor, APCoreMCP.serve() delegates to the module-level
    serve() in apcore_mcp/__init__.py which uses names already bound at import
    time (MCPServerFactory, TransportManager). Patch at those bound locations.
    """
    return (
        patch("apcore_mcp.MCPServerFactory"),
        patch("apcore_mcp.ExecutionRouter"),
        patch("apcore_mcp.TransportManager"),
    )


def _setup_factory_and_tm(mock_factory_cls: MagicMock, mock_tm_cls: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Configure factory and transport mocks, return (mock_factory, mock_tm)."""
    mock_factory = mock_factory_cls.return_value
    mock_factory.create_server.return_value = MagicMock()
    mock_factory.build_tools.return_value = []
    mock_factory.build_init_options.return_value = MagicMock()
    mock_tm = mock_tm_cls.return_value

    async def noop(*a: Any, **kw: Any) -> None:
        pass

    mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: noop())
    mock_tm.run_streamable_http = MagicMock(side_effect=lambda *a, **kw: noop())
    mock_tm.run_sse = MagicMock(side_effect=lambda *a, **kw: noop())
    return mock_factory, mock_tm


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestAPCoreMCPInit:
    """Tests for APCoreMCP constructor."""

    def test_from_registry(self) -> None:
        registry = StubRegistry()
        mcp = APCoreMCP(registry)
        assert mcp.registry is registry
        assert mcp._name == "apcore-mcp"

    def test_from_executor(self) -> None:
        executor = StubExecutor()
        mcp = APCoreMCP(executor)
        assert mcp.registry is executor.registry
        assert mcp.executor is executor

    def test_from_extensions_dir(self) -> None:
        mock_registry = MagicMock(spec=["list", "get_definition", "discover"])
        mock_registry.list.return_value = []
        with patch("apcore.Registry", return_value=mock_registry) as mock_cls:
            mcp = APCoreMCP("/tmp/extensions")
            mock_cls.assert_called_once_with(extensions_dir="/tmp/extensions")
            mock_registry.discover.assert_called_once()
            assert mcp.registry is mock_registry

    def test_from_path_object(self) -> None:
        from pathlib import Path

        mock_registry = StubRegistry()
        with patch("apcore.Registry", return_value=mock_registry) as mock_cls:
            APCoreMCP(Path("/tmp/extensions"))
            mock_cls.assert_called_once_with(extensions_dir="/tmp/extensions")

    def test_custom_name(self) -> None:
        mcp = APCoreMCP(StubRegistry(), name="my-server")
        assert mcp._name == "my-server"

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="name must not be empty"):
            APCoreMCP(StubRegistry(), name="")

    def test_long_name_raises(self) -> None:
        with pytest.raises(ValueError, match="exceeds maximum length"):
            APCoreMCP(StubRegistry(), name="x" * 256)

    def test_empty_tag_raises(self) -> None:
        with pytest.raises(ValueError, match="Tag values must not be empty"):
            APCoreMCP(StubRegistry(), tags=["valid", ""])

    def test_empty_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="prefix must not be empty"):
            APCoreMCP(StubRegistry(), prefix="")

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown log level"):
            APCoreMCP(StubRegistry(), log_level="VERBOSE")

    def test_valid_log_level(self) -> None:
        mcp = APCoreMCP(StubRegistry(), log_level="DEBUG")
        assert mcp._name == "apcore-mcp"

    def test_stores_options(self) -> None:
        auth = MagicMock()
        mcp = APCoreMCP(
            StubRegistry(),
            version="1.0.0",
            tags=["public"],
            prefix="api",
            validate_inputs=True,
            authenticator=auth,
            require_auth=False,
            exempt_paths={"/health"},
        )
        assert mcp._version == "1.0.0"
        assert mcp._tags == ["public"]
        assert mcp._prefix == "api"
        assert mcp._validate_inputs is True
        assert mcp._authenticator is auth
        assert mcp._require_auth is False
        assert mcp._exempt_paths == {"/health"}

    def test_approval_handler_not_stored(self) -> None:
        """approval_handler is passed to Executor, not stored on instance."""
        mcp = APCoreMCP(StubRegistry(), approval_handler=MagicMock())
        assert not hasattr(mcp, "_approval_handler")

    def test_default_output_formatter_is_none(self) -> None:
        """Default output_formatter is None (raw JSON)."""
        mcp = APCoreMCP(StubRegistry())
        assert mcp._output_formatter is None

    def test_output_formatter_none_disables(self) -> None:
        """Setting output_formatter=None disables formatting."""
        mcp = APCoreMCP(StubRegistry(), output_formatter=None)
        assert mcp._output_formatter is None

    def test_output_formatter_custom(self) -> None:
        """Custom output_formatter is stored."""
        custom = lambda d: "custom"  # noqa: E731
        mcp = APCoreMCP(StubRegistry(), output_formatter=custom)
        assert mcp._output_formatter is custom


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


class TestAPCoreMCPProperties:
    """Tests for APCoreMCP properties."""

    def test_tools_lists_module_ids(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        assert sorted(mcp.tools) == ["test.hello", "test.world"]

    def test_tools_with_prefix(self) -> None:
        registry = StubRegistry()
        registry._modules["other.foo"] = StubDescriptor("other.foo")
        mcp = APCoreMCP(registry, prefix="test")
        assert sorted(mcp.tools) == ["test.hello", "test.world"]

    def test_registry_property(self) -> None:
        registry = StubRegistry()
        mcp = APCoreMCP(registry)
        assert mcp.registry is registry

    def test_executor_property(self) -> None:
        registry = StubRegistry()
        mcp = APCoreMCP(registry)
        assert mcp.executor is not None


# ---------------------------------------------------------------------------
# serve() tests
# ---------------------------------------------------------------------------


class TestAPCoreMCPServe:
    """Tests for APCoreMCP.serve()."""

    def test_serve_stdio(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _, mock_tm = _setup_factory_and_tm(mf, mt)
            mcp.serve(transport="stdio")
            mock_tm.run_stdio.assert_called_once()

    def test_serve_streamable_http(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _, mock_tm = _setup_factory_and_tm(mf, mt)
            mcp.serve(transport="streamable-http", host="0.0.0.0", port=9000)
            mock_tm.run_streamable_http.assert_called_once()

    def test_serve_sse(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _, mock_tm = _setup_factory_and_tm(mf, mt)
            mcp.serve(transport="sse")
            mock_tm.run_sse.assert_called_once()

    def test_serve_unknown_transport_raises(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _setup_factory_and_tm(mf, mt)
            with pytest.raises(ValueError, match="Unknown transport"):
                mcp.serve(transport="websocket")

    def test_serve_invalid_explorer_prefix_raises(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        with pytest.raises(ValueError, match="explorer_prefix must start with"):
            mcp.serve(explorer=True, explorer_prefix="no-slash")

    def test_serve_callbacks(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        started = []
        stopped = []
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _setup_factory_and_tm(mf, mt)
            mcp.serve(
                on_startup=lambda: started.append(True),
                on_shutdown=lambda: stopped.append(True),
            )
            assert started == [True]
            assert stopped == [True]

    def test_serve_with_explorer(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt, patch("apcore_mcp.explorer.create_explorer_mount", return_value=MagicMock()):
            _, mock_tm = _setup_factory_and_tm(mf, mt)
            mcp.serve(transport="streamable-http", explorer=True, allow_execute=True)
            mock_tm.run_streamable_http.assert_called_once()

    def test_serve_with_auth(self) -> None:
        auth = MagicMock()
        mcp = APCoreMCP(StubRegistry(), authenticator=auth)
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _, mock_tm = _setup_factory_and_tm(mf, mt)
            mcp.serve(transport="streamable-http")
            call_kwargs = mock_tm.run_streamable_http.call_args
            assert call_kwargs.kwargs.get("middleware") is not None

    def test_on_shutdown_called_on_transport_failure(self) -> None:
        """on_shutdown is called even when the transport raises."""
        mcp = APCoreMCP(StubRegistry())
        shutdown_called = []
        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            _, mock_tm = _setup_factory_and_tm(mf, mt)

            async def failing(*a: Any, **kw: Any) -> None:
                raise RuntimeError("Transport failed")

            mock_tm.run_stdio = MagicMock(side_effect=lambda *a, **kw: failing())

            with pytest.raises(RuntimeError, match="Transport failed"):
                mcp.serve(
                    transport="stdio",
                    on_shutdown=lambda: shutdown_called.append(True),
                )
            assert shutdown_called == [True]

    def test_serve_no_dynamic_parameter(self) -> None:
        """APCoreMCP.serve() does not accept a dynamic parameter."""
        mcp = APCoreMCP(StubRegistry())
        with pytest.raises(TypeError):
            mcp.serve(dynamic=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# to_openai_tools() tests
# ---------------------------------------------------------------------------


class TestAPCoreMCPOpenAI:
    """Tests for APCoreMCP.to_openai_tools()."""

    def test_to_openai_tools(self) -> None:
        registry = StubRegistry()
        mcp = APCoreMCP(registry)

        with patch("apcore_mcp.converters.openai.OpenAIConverter") as mock_converter_cls:
            mock_converter = mock_converter_cls.return_value
            mock_converter.convert_registry.return_value = [{"type": "function", "function": {}}]

            tools = mcp.to_openai_tools()
            assert len(tools) == 1
            mock_converter.convert_registry.assert_called_once_with(
                registry,
                embed_annotations=False,
                strict=False,
                tags=None,
                prefix=None,
            )

    def test_to_openai_tools_with_options(self) -> None:
        registry = StubRegistry()
        mcp = APCoreMCP(registry, tags=["public"], prefix="api")

        with patch("apcore_mcp.converters.openai.OpenAIConverter") as mock_converter_cls:
            mock_converter = mock_converter_cls.return_value
            mock_converter.convert_registry.return_value = []

            mcp.to_openai_tools(embed_annotations=True, strict=True)
            mock_converter.convert_registry.assert_called_once_with(
                registry,
                embed_annotations=True,
                strict=True,
                tags=["public"],
                prefix="api",
            )


# ---------------------------------------------------------------------------
# async_serve() tests
# ---------------------------------------------------------------------------


class TestAPCoreMCPAsyncServe:
    """Tests for APCoreMCP.async_serve()."""

    @pytest.mark.asyncio
    async def test_async_serve_yields_app(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        mock_app = MagicMock()

        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2, p3 as mt:
            mock_factory, mock_tm = _setup_factory_and_tm(mf, mt)

            import contextlib

            @contextlib.asynccontextmanager
            async def fake_build(*a: Any, **kw: Any) -> Any:
                yield mock_app

            mock_tm.build_streamable_http_app = fake_build

            async with mcp.async_serve() as app:
                assert app is mock_app

    @pytest.mark.asyncio
    async def test_async_serve_invalid_explorer_prefix(self) -> None:
        mcp = APCoreMCP(StubRegistry())
        with pytest.raises(ValueError, match="explorer_prefix must start with"):
            async with mcp.async_serve(explorer=True, explorer_prefix="bad"):
                pass


# ---------------------------------------------------------------------------
# Py-C2: APCoreMCP.serve()/async_serve() silently drop output_formatter
# ---------------------------------------------------------------------------


class TestPyC2OutputFormatterPropagation:
    """[Py-C2] output_formatter set at construction time must reach ExecutionRouter."""

    def test_serve_passes_output_formatter_to_router(self) -> None:
        """APCoreMCP.serve() must pass self._output_formatter to the module-level serve()."""
        mock_formatter = MagicMock(return_value="formatted")
        mcp = APCoreMCP(StubRegistry(), output_formatter=mock_formatter)

        captured_formatter: list[Any] = []

        p1, p2, p3 = _make_serve_patches()
        with p1 as mf, p2 as mock_router_cls, p3 as mt:
            _, _ = _setup_factory_and_tm(mf, mt)

            # Capture the kwargs passed to ExecutionRouter
            def capture_router(*args: Any, **kwargs: Any) -> MagicMock:
                captured_formatter.append(kwargs.get("output_formatter"))
                router_mock = MagicMock()
                router_mock.register_tools = MagicMock()
                return router_mock

            mock_router_cls.side_effect = capture_router
            mcp.serve(transport="stdio")

        assert len(captured_formatter) == 1, "ExecutionRouter was not instantiated"
        assert (
            captured_formatter[0] is mock_formatter
        ), f"output_formatter was not passed to ExecutionRouter: got {captured_formatter[0]!r}"

    @pytest.mark.asyncio
    async def test_async_serve_passes_output_formatter_to_router(self) -> None:
        """APCoreMCP.async_serve() must pass self._output_formatter to ExecutionRouter."""
        mock_formatter = MagicMock(return_value="formatted")
        mcp = APCoreMCP(StubRegistry(), output_formatter=mock_formatter)

        captured_formatter: list[Any] = []

        import contextlib

        mock_app = MagicMock()

        with (
            patch("apcore_mcp.MCPServerFactory") as mf,
            patch("apcore_mcp.ExecutionRouter") as mock_router_cls,
            patch("apcore_mcp.TransportManager") as mt,
        ):
            mock_factory = mf.return_value
            mock_factory.create_server.return_value = MagicMock()
            mock_factory.build_tools.return_value = []
            mock_factory.build_init_options.return_value = MagicMock()

            mock_tm = mt.return_value

            @contextlib.asynccontextmanager
            async def fake_build_app(*a: Any, **kw: Any) -> Any:
                yield mock_app

            mock_tm.build_streamable_http_app = fake_build_app

            def capture_router(*args: Any, **kwargs: Any) -> MagicMock:
                captured_formatter.append(kwargs.get("output_formatter"))
                router_mock = MagicMock()
                router_mock.register_tools = MagicMock()
                return router_mock

            mock_router_cls.side_effect = capture_router

            async with mcp.async_serve():
                pass

        assert len(captured_formatter) == 1, "ExecutionRouter was not instantiated"
        assert (
            captured_formatter[0] is mock_formatter
        ), f"output_formatter was not passed to ExecutionRouter in async_serve: got {captured_formatter[0]!r}"
