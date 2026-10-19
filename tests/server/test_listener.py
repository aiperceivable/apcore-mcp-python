"""Unit tests for RegistryListener."""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest
from mcp import types as mcp_types

from apcore_mcp.server.factory import MCPServerFactory
from apcore_mcp.server.listener import RegistryListener
from tests.conftest import ModuleAnnotations, ModuleDescriptor

# ---------------------------------------------------------------------------
# Stub Registry (with event callback support)
# ---------------------------------------------------------------------------


class StubRegistry:
    """Stub for apcore Registry with event callback support."""

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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> StubRegistry:
    return StubRegistry()


@pytest.fixture
def factory() -> MCPServerFactory:
    return MCPServerFactory()


@pytest.fixture
def listener(registry: StubRegistry, factory: MCPServerFactory) -> RegistryListener:
    return RegistryListener(registry=registry, factory=factory)


def _make_descriptor(module_id: str = "test.tool", description: str = "A test tool") -> ModuleDescriptor:
    """Helper to create a simple descriptor."""
    return ModuleDescriptor(
        module_id=module_id,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "A value"},
            },
            "required": ["value"],
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
        },
        annotations=ModuleAnnotations(readonly=True),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitialization:
    """Tests for RegistryListener initialization."""

    def test_listener_can_be_created(self, registry: StubRegistry, factory: MCPServerFactory) -> None:
        """RegistryListener can be created with a registry and factory."""
        listener = RegistryListener(registry=registry, factory=factory)
        assert listener is not None

    def test_tools_empty_initially(self, listener: RegistryListener) -> None:
        """The tools dict is empty before any events are fired."""
        assert listener.tools == {}


class TestStartStop:
    """Tests for start() and stop() lifecycle."""

    def test_start_registers_callbacks(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """start() registers 'register' and 'unregister' callbacks on the registry."""
        listener.start()
        assert "register" in registry._callbacks
        assert len(registry._callbacks["register"]) == 1
        assert "unregister" in registry._callbacks
        assert len(registry._callbacks["unregister"]) == 1

    def test_start_is_idempotent(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """Calling start() twice only registers callbacks once."""
        listener.start()
        listener.start()
        assert len(registry._callbacks["register"]) == 1
        assert len(registry._callbacks["unregister"]) == 1

    def test_stop_deactivates_callbacks(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """After stop(), event callbacks become no-ops."""
        descriptor = _make_descriptor("stopped.tool")
        registry.add_definition(descriptor)
        listener.start()
        listener.stop()

        # Trigger register event after stop -- should be ignored
        registry.trigger("register", "stopped.tool")
        assert listener.tools == {}


class TestOnRegister:
    """Tests for _on_register callback behavior."""

    def test_on_register_adds_tool(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """When a module is registered, the listener builds and adds a Tool."""
        descriptor = _make_descriptor("image.resize", "Resize an image")
        registry.add_definition(descriptor)
        listener.start()

        registry.trigger("register", "image.resize")

        tools = listener.tools
        assert "image.resize" in tools
        assert isinstance(tools["image.resize"], mcp_types.Tool)
        assert tools["image.resize"].name == "image.resize"
        assert tools["image.resize"].description == "Resize an image"

    def test_on_register_none_definition_logs_warning(
        self,
        listener: RegistryListener,
        registry: StubRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If get_definition returns None, logs a warning and adds no tool."""
        listener.start()

        # Do NOT add a definition -- get_definition will return None
        with caplog.at_level(logging.WARNING):
            registry.trigger("register", "ghost.module")

        assert listener.tools == {}
        assert "Definition not found for registered module: ghost.module" in caplog.text

    def test_on_register_build_tool_error_logs_warning(
        self,
        listener: RegistryListener,
        registry: StubRegistry,
        factory: MCPServerFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If build_tool raises, logs a warning and adds no tool."""
        # Create a descriptor that will cause build_tool to fail
        bad_descriptor = ModuleDescriptor(
            module_id="bad.module",
            description="will fail",
            input_schema=["invalid schema"],
            output_schema={},
        )
        registry.add_definition(bad_descriptor)
        listener.start()

        with caplog.at_level(logging.WARNING):
            registry.trigger("register", "bad.module")

        assert listener.tools == {}
        assert "Failed to build tool for bad.module" in caplog.text

    def test_on_register_swallows_a_raising_get_definition(
        self,
        listener: RegistryListener,
        registry: StubRegistry,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """[A-D-RL-3] A raising registry.get_definition must not escape the callback.

        The callback runs on the registry's synchronous event dispatcher, so an
        exception here propagates into the registry.register() call that fired
        the event. Contract: no exceptions raised, all failures logged.
        """

        def boom(module_id: str) -> ModuleDescriptor | None:
            raise RuntimeError("registry unavailable")

        registry.get_definition = boom  # type: ignore[method-assign]
        listener.start()

        with caplog.at_level(logging.WARNING):
            registry.trigger("register", "some.module")

        assert listener.tools == {}
        assert "Failed to build tool for some.module" in caplog.text


class TestOnUnregister:
    """Tests for _on_unregister callback behavior."""

    def test_on_unregister_removes_tool(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """Unregistering a module removes it from the tools dict."""
        descriptor = _make_descriptor("temp.tool")
        registry.add_definition(descriptor)
        listener.start()

        registry.trigger("register", "temp.tool")
        assert "temp.tool" in listener.tools

        registry.trigger("unregister", "temp.tool")
        assert "temp.tool" not in listener.tools

    def test_on_unregister_unknown_module_is_silent(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """Unregistering an unknown module_id does not raise or add entries."""
        listener.start()

        # Should not raise
        registry.trigger("unregister", "nonexistent.tool")
        assert listener.tools == {}


class TestToolsProperty:
    """Tests for the tools property snapshot behavior."""

    def test_tools_returns_snapshot(self, listener: RegistryListener, registry: StubRegistry) -> None:
        """Modifying the returned dict does not affect internal state."""
        descriptor = _make_descriptor("snapshot.tool")
        registry.add_definition(descriptor)
        listener.start()

        registry.trigger("register", "snapshot.tool")

        # Get a snapshot and mutate it
        snapshot = listener.tools
        snapshot.pop("snapshot.tool")
        snapshot["injected"] = "bad"  # type: ignore[assignment]

        # Internal state should be unchanged
        internal = listener.tools
        assert "snapshot.tool" in internal
        assert "injected" not in internal


class TestThreadSafety:
    """Tests for concurrent register/unregister operations."""

    def test_concurrent_register_unregister(self, registry: StubRegistry, factory: MCPServerFactory) -> None:
        """Concurrent register and unregister operations do not corrupt state."""
        listener = RegistryListener(registry=registry, factory=factory)
        listener.start()

        num_modules = 100
        # Pre-add all descriptors so get_definition succeeds
        for i in range(num_modules):
            registry.add_definition(_make_descriptor(f"concurrent.tool.{i}"))

        errors: list[Exception] = []

        def register_batch(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    registry.trigger("register", f"concurrent.tool.{i}")
            except Exception as e:
                errors.append(e)

        def unregister_batch(start: int, end: int) -> None:
            try:
                for i in range(start, end):
                    registry.trigger("unregister", f"concurrent.tool.{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        # Register first half while unregistering concurrently
        t1 = threading.Thread(target=register_batch, args=(0, num_modules))
        t2 = threading.Thread(target=unregister_batch, args=(0, num_modules // 2))
        threads.extend([t1, t2])

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # No exceptions should have been raised
        assert errors == [], f"Concurrent operations raised errors: {errors}"

        # The tools dict should be in a consistent state (a valid dict, no corruption)
        tools = listener.tools
        assert isinstance(tools, dict)
        # All values should be Tool instances
        for tool in tools.values():
            assert isinstance(tool, mcp_types.Tool)
