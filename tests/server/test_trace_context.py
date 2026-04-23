"""Tests for W3C trace_context propagation in ExecutionRouter."""

from __future__ import annotations

import json
from typing import Any

import pytest

from apcore_mcp.server.router import ExecutionRouter


class RecordingExecutor:
    """Executor that records the context it receives."""

    def __init__(self) -> None:
        self.last_context: Any = None

    async def call_async(
        self,
        module_id: str,
        inputs: dict[str, Any] | None = None,
        context: Any = None,
    ) -> dict[str, Any]:
        self.last_context = context
        return {"ok": True}


@pytest.mark.asyncio
async def test_inbound_traceparent_seeds_context_trace_id() -> None:
    """A well-formed _meta.traceparent pins the trace_id on the apcore Context."""
    ex = RecordingExecutor()
    router = ExecutionRouter(ex)
    tp = "00-11112222333344445555666677778888-aaaabbbbccccdddd-01"

    content, is_error, trace_id = await router.handle_call(
        "any",
        {},
        extra={"_meta": {"traceparent": tp}},
    )

    assert is_error is False
    assert ex.last_context is not None
    assert ex.last_context.trace_id == "11112222333344445555666677778888"
    assert trace_id == "11112222333344445555666677778888"


@pytest.mark.asyncio
async def test_outbound_traceparent_attached_to_first_content_meta() -> None:
    """Response content items carry _meta.traceparent for client correlation."""
    ex = RecordingExecutor()
    router = ExecutionRouter(ex)

    content, is_error, trace_id = await router.handle_call("m", {})

    assert is_error is False
    assert content[0].get("_meta") is not None
    outbound = content[0]["_meta"]["traceparent"]
    assert outbound.startswith("00-")
    parts = outbound.split("-")
    assert len(parts) == 4
    assert parts[1] == trace_id


@pytest.mark.asyncio
async def test_malformed_traceparent_is_ignored_regenerated() -> None:
    """A malformed header falls back to a newly generated trace_id."""
    ex = RecordingExecutor()
    router = ExecutionRouter(ex)

    _, _, trace_id = await router.handle_call(
        "m",
        {},
        extra={"_meta": {"traceparent": "not-a-traceparent"}},
    )

    # Not the input — regenerated.
    assert trace_id is not None
    assert trace_id != "not-a-traceparent"
    assert len(trace_id) == 32


@pytest.mark.asyncio
async def test_round_trip_preserves_trace_id() -> None:
    """Inbound trace_id equals outbound trace_id (minus parent_id regeneration)."""
    ex = RecordingExecutor()
    router = ExecutionRouter(ex)
    tp = "00-deadbeefdeadbeefdeadbeefdeadbeef-aaaabbbbccccdddd-01"

    content, _, trace_id = await router.handle_call("m", {}, extra={"_meta": {"traceparent": tp}})

    outbound = content[0]["_meta"]["traceparent"]
    assert outbound.split("-")[1] == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert trace_id == "deadbeefdeadbeefdeadbeefdeadbeef"
    # Result JSON itself is untouched — trace lives only in _meta.
    assert json.loads(content[0]["text"]) == {"ok": True}
