"""Tests for the Async Task Bridge (F-043).

Covers: async-hint detection, submit envelope, status/cancel/list meta-tools,
progress fan-out, reserved-prefix rejection in factory.build_tool, and the
TaskLimitExceededError error path.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from apcore.async_task import AsyncTaskManager, TaskStatus
from apcore.errors import TaskLimitExceededError

from apcore_mcp.server.async_task_bridge import META_TOOL_NAMES, AsyncTaskBridge
from apcore_mcp.server.factory import MCPServerFactory


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _Annotations:
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Descriptor:
    module_id: str
    description: str = "x"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    annotations: _Annotations | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class _SlowExecutor:
    """Executor whose call_async awaits an event before completing."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any] | None, Any]] = []

    async def call_async(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((module_id, inputs, context))
        await self.release.wait()
        return {"done": True, "module": module_id}


# ---------------------------------------------------------------------------
# is_async_module detection
# ---------------------------------------------------------------------------


def test_is_async_module_metadata_bool_true() -> None:
    d = _Descriptor(module_id="m", metadata={"async": True})
    assert AsyncTaskBridge.is_async_module(d) is True


def test_is_async_module_annotations_extra_string() -> None:
    d = _Descriptor(module_id="m", annotations=_Annotations(extra={"mcp_async": "true"}))
    assert AsyncTaskBridge.is_async_module(d) is True


def test_is_async_module_not_hinted() -> None:
    d = _Descriptor(module_id="m", metadata={"async": False})
    assert AsyncTaskBridge.is_async_module(d) is False
    assert AsyncTaskBridge.is_async_module(None) is False


# ---------------------------------------------------------------------------
# Submit + status round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_returns_pending_envelope() -> None:
    executor = _SlowExecutor()
    mgr = AsyncTaskManager(executor)
    bridge = AsyncTaskBridge(mgr)

    envelope = await bridge.submit("m", {"x": 1}, None)
    assert envelope["status"] == TaskStatus.PENDING.value
    assert isinstance(envelope["task_id"], str)

    # Release and wait for completion.
    executor.release.set()
    await asyncio.sleep(0.01)
    info = mgr.get_status(envelope["task_id"])
    assert info is not None
    assert info.status in (TaskStatus.COMPLETED, TaskStatus.RUNNING)


@pytest.mark.asyncio
async def test_status_tool_returns_result_when_completed() -> None:
    executor = _SlowExecutor()
    executor.release.set()  # resolve immediately
    mgr = AsyncTaskManager(executor)
    bridge = AsyncTaskBridge(mgr)

    envelope = await bridge.submit("m", {}, None)
    task_id = envelope["task_id"]
    # Poll briefly for completion.
    for _ in range(20):
        if mgr.get_status(task_id).status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.005)

    content, is_error, _ = await bridge.handle_meta_tool(
        "__apcore_task_status", {"task_id": task_id}
    )
    assert is_error is False
    body = json.loads(content[0]["text"])
    assert body["status"] == "completed"
    assert body["result"] == {"done": True, "module": "m"}


@pytest.mark.asyncio
async def test_status_tool_unknown_task_id() -> None:
    mgr = AsyncTaskManager(_SlowExecutor())
    bridge = AsyncTaskBridge(mgr)
    content, is_error, _ = await bridge.handle_meta_tool(
        "__apcore_task_status", {"task_id": "missing"}
    )
    assert is_error is True
    body = json.loads(content[0]["text"])
    assert body["error"] == "ASYNC_TASK_NOT_FOUND"


# ---------------------------------------------------------------------------
# Cancel + list meta-tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_tool_cancels_running_task() -> None:
    executor = _SlowExecutor()
    mgr = AsyncTaskManager(executor)
    bridge = AsyncTaskBridge(mgr)

    envelope = await bridge.submit("m", {}, None)
    task_id = envelope["task_id"]

    content, is_error, _ = await bridge.handle_meta_tool(
        "__apcore_task_cancel", {"task_id": task_id}
    )
    assert is_error is False
    body = json.loads(content[0]["text"])
    assert body["task_id"] == task_id
    assert body["cancelled"] is True
    assert mgr.get_status(task_id).status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_list_tool_filters_by_status() -> None:
    executor = _SlowExecutor()
    executor.release.set()
    mgr = AsyncTaskManager(executor)
    bridge = AsyncTaskBridge(mgr)

    await bridge.submit("m1", {}, None)
    await bridge.submit("m2", {}, None)
    await asyncio.sleep(0.02)  # let tasks finish

    content, _, _ = await bridge.handle_meta_tool("__apcore_task_list", {"status": "completed"})
    body = json.loads(content[0]["text"])
    assert len(body["tasks"]) == 2
    assert all(t["status"] == "completed" for t in body["tasks"])


# ---------------------------------------------------------------------------
# Submit meta-tool rejects non-async modules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_tool_rejects_non_async_module() -> None:
    executor = _SlowExecutor()
    bridge = AsyncTaskBridge(AsyncTaskManager(executor))
    sync_desc = _Descriptor(module_id="m", metadata={})

    content, is_error, _ = await bridge.handle_meta_tool(
        "__apcore_task_submit",
        {"module_id": "m", "arguments": {}},
        resolve_descriptor=lambda mid: sync_desc,
    )
    assert is_error is True
    body = json.loads(content[0]["text"])
    assert body["error"] == "ASYNC_MODULE_NOT_ASYNC"


# ---------------------------------------------------------------------------
# Capacity error surfaces via ErrorMapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_limit_exceeded_mapped() -> None:
    executor = _SlowExecutor()
    mgr = AsyncTaskManager(executor, max_tasks=1)
    bridge = AsyncTaskBridge(mgr)

    await bridge.submit("m", {}, None)  # fills the slot
    async_desc = _Descriptor(module_id="m", metadata={"async": True})

    # Second submit via meta-tool should surface the mapped error.
    content, is_error, _ = await bridge.handle_meta_tool(
        "__apcore_task_submit",
        {"module_id": "m", "arguments": {}},
        resolve_descriptor=lambda mid: async_desc,
    )
    assert is_error is True
    # Error text contains the apcore message; we only assert it's non-empty.
    assert content[0]["text"]


# ---------------------------------------------------------------------------
# Progress fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_fanout_binds_token_and_sink() -> None:
    executor = _SlowExecutor()
    mgr = AsyncTaskManager(executor)
    bridge = AsyncTaskBridge(mgr)

    received: list[dict[str, Any]] = []

    async def send(notification: dict[str, Any]) -> None:
        received.append(notification)

    from apcore import Context

    ctx = Context.create()
    await bridge._submit_with_progress(
        "m", {}, ctx, progress_token="tok-1", send_notification=send
    )
    # Simulate the module invoking the installed progress callback.
    cb = ctx.data["_mcp_progress"]
    await cb(0.5, 1.0, "halfway")

    assert len(received) == 1
    assert received[0]["method"] == "notifications/progress"
    assert received[0]["params"]["progressToken"] == "tok-1"
    assert received[0]["params"]["progress"] == 0.5


# ---------------------------------------------------------------------------
# Reserved-prefix rejection in factory
# ---------------------------------------------------------------------------


def test_factory_rejects_reserved_prefix() -> None:
    factory = MCPServerFactory()
    desc = _Descriptor(module_id="__apcore_evil", description="x")

    with pytest.raises(ValueError) as excinfo:
        factory.build_tool(desc)
    assert "reserved prefix" in str(excinfo.value)


def test_meta_tool_names_match_spec() -> None:
    bridge = AsyncTaskBridge(AsyncTaskManager(_SlowExecutor()))
    names = [t.name for t in bridge.build_meta_tools()]
    assert set(names) == set(META_TOOL_NAMES)


@pytest.mark.asyncio
async def test_submit_raises_task_limit_directly() -> None:
    """AsyncTaskManager.submit raises TaskLimitExceededError (apcore 0.19)."""
    executor = _SlowExecutor()
    mgr = AsyncTaskManager(executor, max_tasks=1)
    bridge = AsyncTaskBridge(mgr)
    await bridge.submit("a", {}, None)
    with pytest.raises(TaskLimitExceededError):
        await bridge.submit("b", {}, None)
