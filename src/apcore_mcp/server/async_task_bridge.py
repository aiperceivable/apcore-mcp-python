"""Async Task Bridge: route async-hinted module calls to apcore's AsyncTaskManager.

Implements F-043 (``docs/features/async-task-bridge.md``): detects async-hinted
modules at dispatch, routes them through :class:`apcore.async_task.AsyncTaskManager`,
and registers four reserved meta-tools (``__apcore_task_*``) that MCP clients use
to submit, poll, cancel, and list background tasks.

Progress notifications fan out when the caller supplies ``_meta.progressToken``;
the bridge binds ``task_id -> progressToken`` and attaches a progress callback to
the execution context for the duration of the task.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from apcore import Context
from apcore.async_task import AsyncTaskManager, TaskInfo, TaskStatus
from apcore.errors import TaskLimitExceededError
from apcore.trace_context import TraceContext, TraceParent
from mcp import types as mcp_types

from apcore_mcp.adapters.errors import ErrorMapper
from apcore_mcp.helpers import MCP_PROGRESS_KEY

logger = logging.getLogger(__name__)

__all__ = ["AsyncTaskBridge", "RESERVED_PREFIX", "META_TOOL_NAMES"]

RESERVED_PREFIX = "__apcore_"

META_TOOL_NAMES = (
    "__apcore_task_submit",
    "__apcore_task_status",
    "__apcore_task_cancel",
    "__apcore_task_list",
)


def _is_async_hint_truthy(value: Any) -> bool:
    """Async hint follows spec: metadata.async=True OR extra.mcp_async='true'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _task_info_to_dict(info: TaskInfo) -> dict[str, Any]:
    """Project a :class:`TaskInfo` to a JSON-safe dict (spec §Outputs)."""
    return {
        "task_id": info.task_id,
        "module_id": info.module_id,
        "status": info.status.value,
        "submitted_at": info.submitted_at,
        "started_at": info.started_at,
        "completed_at": info.completed_at,
    }


class AsyncTaskBridge:
    """Thin routing layer in front of :class:`AsyncTaskManager`.

    Owns:
    - async-hint detection on ``ModuleDescriptor``.
    - submission path returning ``{"task_id", "status": "pending"}``.
    - meta-tool registration and handler dispatch.
    - progress fan-out via ``task_id -> progressToken`` mapping.
    """

    def __init__(
        self,
        manager: AsyncTaskManager,
        *,
        redactor: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._manager = manager
        self._redactor = redactor
        self._error_mapper = ErrorMapper()
        # Maps task_id -> (progress_token, send_notification) for fan-out.
        self._progress_bindings: dict[str, tuple[Any, Callable[[dict[str, Any]], Awaitable[None]]]] = {}

    @property
    def manager(self) -> AsyncTaskManager:
        return self._manager

    @staticmethod
    def is_async_module(descriptor: Any) -> bool:
        """Return True if the module descriptor carries an async hint.

        Hints per spec ``async.hint_keys``:
        - ``metadata.async == True``
        - ``annotations.extra["mcp_async"] == "true"``
        """
        if descriptor is None:
            return False
        metadata = getattr(descriptor, "metadata", None) or {}
        if isinstance(metadata, dict) and _is_async_hint_truthy(metadata.get("async")):
            return True
        annotations = getattr(descriptor, "annotations", None)
        if annotations is not None:
            extra = getattr(annotations, "extra", None) or {}
            if isinstance(extra, dict) and _is_async_hint_truthy(extra.get("mcp_async")):
                return True
        return False

    async def submit(
        self,
        module_id: str,
        arguments: dict[str, Any],
        context: Context | None,
        *,
        progress_token: Any | None = None,
        send_notification: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Submit *module_id* to the task manager; returns ``{task_id, status}`` envelope."""
        task_id = await self._submit_with_progress(
            module_id, arguments, context, progress_token=progress_token, send_notification=send_notification
        )
        return {"task_id": task_id, "status": TaskStatus.PENDING.value}

    async def _submit_with_progress(
        self,
        module_id: str,
        arguments: dict[str, Any],
        context: Context | None,
        *,
        progress_token: Any | None = None,
        send_notification: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        """Submit to AsyncTaskManager; attach progress sink before submit."""
        # Bind progress callback onto context so module-emitted events
        # route through MCP notifications/progress.
        if context is not None and progress_token is not None and send_notification is not None:
            self._install_progress_sink(context, progress_token, send_notification)
        task_id = await self._manager.submit(module_id, arguments, context)
        if progress_token is not None and send_notification is not None:
            self._progress_bindings[task_id] = (progress_token, send_notification)
        return task_id

    def _install_progress_sink(
        self,
        context: Context,
        progress_token: Any,
        send_notification: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Inject a progress callback onto the context for fan-out."""
        _pt = progress_token
        _sn = send_notification

        async def _progress_callback(
            progress: float,
            total: float | None = None,
            message: str | None = None,
        ) -> None:
            try:
                params: dict[str, Any] = {
                    "progressToken": _pt,
                    "progress": progress,
                    "total": total if total is not None else 0,
                }
                if message is not None:
                    params["message"] = message
                await _sn({"method": "notifications/progress", "params": params})
            except Exception:
                # Spec: progress sink failures are logged and swallowed.
                logger.warning("progress fan-out failed", exc_info=True)

        context.data[MCP_PROGRESS_KEY] = _progress_callback

    # ── Meta-tool surface ───────────────────────────────────────────────

    def build_meta_tools(self) -> list[mcp_types.Tool]:
        """Return the four reserved ``__apcore_task_*`` MCP Tool objects."""
        return [
            mcp_types.Tool(
                name="__apcore_task_submit",
                description="Submit a module for background execution. Returns {task_id, status:'pending'}.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "module_id": {"type": "string"},
                        "arguments": {"type": "object"},
                        "version_hint": {"type": "string"},
                    },
                    "required": ["module_id", "arguments"],
                    "additionalProperties": False,
                },
            ),
            mcp_types.Tool(
                name="__apcore_task_status",
                description="Fetch TaskInfo for a task_id. Includes result when completed, error when failed.",
                inputSchema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            ),
            mcp_types.Tool(
                name="__apcore_task_cancel",
                description="Cancel a pending or running task.",
                inputSchema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            ),
            mcp_types.Tool(
                name="__apcore_task_list",
                description="List tracked tasks, optionally filtered by status.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "running", "completed", "failed", "cancelled"],
                        }
                    },
                    "additionalProperties": False,
                },
            ),
        ]

    @staticmethod
    def is_meta_tool(name: str) -> bool:
        return name in META_TOOL_NAMES

    async def handle_meta_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        resolve_descriptor: Callable[[str], Any] | None = None,
        router_extra: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Dispatch a meta-tool call. Returns ``(content, is_error, trace_id)``."""
        args = arguments or {}
        try:
            if name == "__apcore_task_submit":
                return await self._handle_submit_tool(args, resolve_descriptor, router_extra or {})
            if name == "__apcore_task_status":
                return self._handle_status_tool(args)
            if name == "__apcore_task_cancel":
                return await self._handle_cancel_tool(args)
            if name == "__apcore_task_list":
                return self._handle_list_tool(args)
        except TaskLimitExceededError as exc:
            return self._error_response(exc)
        except Exception as exc:
            logger.exception("meta-tool %s failed", name)
            return self._error_response(exc)
        return self._error_response(ValueError(f"Unknown meta-tool: {name}"))

    async def _handle_submit_tool(
        self,
        args: dict[str, Any],
        resolve_descriptor: Callable[[str], Any] | None,
        extra: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        module_id = args.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            return self._error_response(ValueError("module_id is required"))
        # Guard: reject wrapping non-async modules in async submission.
        if resolve_descriptor is not None:
            descriptor = resolve_descriptor(module_id)
            if descriptor is None or not self.is_async_module(descriptor):
                return self._text_response(
                    {
                        "error": "ASYNC_MODULE_NOT_ASYNC",
                        "message": f"Module {module_id!r} is not async-hinted",
                    },
                    is_error=True,
                )
        raw_args = args.get("arguments") or {}
        if not isinstance(raw_args, dict):
            return self._error_response(ValueError("arguments must be an object"))
        context = self._build_context(extra)
        envelope = await self.submit(
            module_id,
            raw_args,
            context,
            progress_token=extra.get("progress_token"),
            send_notification=extra.get("send_notification"),
        )
        return self._text_response(envelope)

    def _handle_status_tool(self, args: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, str | None]:
        task_id = args.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return self._error_response(ValueError("task_id is required"))
        info = self._manager.get_status(task_id)
        if info is None:
            return self._text_response(
                {"error": "ASYNC_TASK_NOT_FOUND", "task_id": task_id},
                is_error=True,
            )
        payload = _task_info_to_dict(info)
        if info.status == TaskStatus.COMPLETED:
            result = info.result
            if self._redactor is not None:
                try:
                    result = self._redactor(info.module_id, result)
                except Exception:
                    logger.debug("task-result redactor raised", exc_info=True)
            payload["result"] = result
        elif info.status == TaskStatus.FAILED:
            payload["error"] = info.error
        return self._text_response(payload)

    async def _handle_cancel_tool(self, args: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, str | None]:
        task_id = args.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return self._error_response(ValueError("task_id is required"))
        if self._manager.get_status(task_id) is None:
            return self._text_response(
                {"error": "ASYNC_TASK_NOT_FOUND", "task_id": task_id},
                is_error=True,
            )
        cancelled = await self._manager.cancel(task_id)
        # Drop any progress binding so leftover callbacks cannot fire.
        self._progress_bindings.pop(task_id, None)
        return self._text_response({"task_id": task_id, "cancelled": cancelled})

    def _handle_list_tool(self, args: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, str | None]:
        status_filter: TaskStatus | None = None
        raw_status = args.get("status")
        if isinstance(raw_status, str):
            try:
                status_filter = TaskStatus(raw_status)
            except ValueError:
                return self._error_response(ValueError(f"Invalid status filter: {raw_status!r}"))
        tasks = self._manager.list_tasks(status_filter)
        return self._text_response({"tasks": [_task_info_to_dict(t) for t in tasks]})

    # ── Helpers ─────────────────────────────────────────────────────────

    def _build_context(self, extra: dict[str, Any]) -> Context:
        """Build a Context from the factory-provided extra dict (identity + traceparent)."""
        identity = extra.get("identity")
        trace_parent: TraceParent | None = None
        meta = extra.get("_meta")
        if isinstance(meta, dict):
            raw_tp = meta.get("traceparent")
            if isinstance(raw_tp, str):
                trace_parent = TraceContext.extract({"traceparent": raw_tp})
        return Context.create(data={}, identity=identity, trace_parent=trace_parent)

    def _text_response(
        self,
        payload: Any,
        *,
        is_error: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        text = json.dumps(payload, default=str)
        return ([{"type": "text", "text": text}], is_error, None)

    def _error_response(self, error: Exception) -> tuple[list[dict[str, Any]], bool, str | None]:
        info = self._error_mapper.to_mcp_error(error)
        return ([{"type": "text", "text": info["message"]}], True, None)

    async def shutdown(self) -> None:
        """Cancel tracked tasks. Delegates to :meth:`AsyncTaskManager.shutdown`."""
        await self._manager.shutdown()
