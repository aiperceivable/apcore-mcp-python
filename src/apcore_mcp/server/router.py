"""ExecutionRouter: route MCP tool calls -> apcore Executor pipeline."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apcore import Context
from apcore.trace_context import TraceContext, TraceParent

from apcore_mcp.adapters.errors import ErrorMapper
from apcore_mcp.helpers import MCP_ELICIT_KEY, MCP_PROGRESS_KEY

logger = logging.getLogger(__name__)

_DEEP_MERGE_MAX_DEPTH = 32


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Recursively merge *overlay* into *base*, capped at ``_DEEP_MERGE_MAX_DEPTH``.

    When both sides have a dict for the same key the merge recurses.
    All other types are overwritten by *overlay*.
    """
    if depth >= _DEEP_MERGE_MAX_DEPTH:
        return {**base, **overlay}
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value, depth + 1)
        else:
            merged[key] = value
    return merged


class ExecutionRouter:
    """Routes MCP tool calls through the apcore Executor pipeline.

    The router sits between the MCP server's call_tool handler and the
    apcore Executor.  It delegates to ``executor.call_async()`` and
    converts the result (or any exception) into a
    ``(content, is_error, trace_id)`` tuple that the MCP factory can
    pass directly to ``CallToolResult``.

    When the executor also exposes an async ``stream()`` method **and**
    the caller provides a ``progress_token`` + ``send_notification``
    callback via the *extra* dict, the router iterates the async
    generator and forwards each chunk as a ``notifications/progress``
    message, accumulating chunks via recursive deep merge.

    Args:
        executor: An apcore Executor instance (duck-typed -- must expose
            an async ``call_async(module_id, inputs)`` method and
            optionally an async ``stream(module_id, inputs)`` generator).
        validate_inputs: Validate tool inputs against schemas before execution.
        output_formatter: Optional callable ``(dict) -> str`` that formats
            execution results into text for LLM consumption.  When None,
            results are serialised with ``json.dumps(result, default=str)``.
    """

    def __init__(
        self,
        executor: Any,
        *,
        validate_inputs: bool = False,
        output_formatter: Callable[[dict[str, Any]], str] | None = None,
        redact_output: bool = True,
        output_schema_map: dict[str, dict] | None = None,
        trace: bool = False,
    ) -> None:
        self._executor = executor
        self._error_mapper = ErrorMapper()
        self._validate_inputs = validate_inputs
        self._output_formatter = output_formatter
        self._redact_output = redact_output
        self._output_schema_map = output_schema_map or {}
        self._trace = trace

        # Cache whether executor methods accept a context parameter,
        # so we avoid a broad TypeError catch on every call.
        self._call_async_accepts_context = self._check_accepts_context(executor.call_async)
        self._stream_accepts_context = self._check_accepts_context(getattr(executor, "stream", None))

    def _maybe_redact(self, tool_name: str, result: Any) -> Any:
        """Apply output redaction if enabled and an output_schema exists for the tool.

        Uses ``apcore.redact_sensitive()`` to strip sensitive fields.
        Fails open: if redaction raises, the original result is returned.
        """
        if not self._redact_output:
            return result
        output_schema = self._output_schema_map.get(tool_name)
        if output_schema is None:
            return result
        try:
            from apcore import redact_sensitive

            return redact_sensitive(result, output_schema)
        except Exception:
            logger.warning("redact_sensitive failed for %s, returning unredacted output", tool_name, exc_info=True)
            return result

    def _format_result(self, result: Any) -> str:
        """Format an execution result into text for LLM consumption.

        Uses the configured output_formatter if set, otherwise falls back
        to ``json.dumps(result, default=str)``.

        The formatter is only applied to dict results. Non-dict results
        (str, list, etc.) are always serialised with json.dumps.
        """
        if self._output_formatter is not None and isinstance(result, dict):
            try:
                return self._output_formatter(result)
            except Exception:
                logger.debug("output_formatter failed, falling back to json.dumps", exc_info=True)
        return json.dumps(result, default=str)

    @staticmethod
    def _check_accepts_context(method: Any) -> bool:
        """Return True if *method* accepts at least 3 positional parameters
        (excluding ``self``), i.e. (tool_name, arguments, context)."""
        if method is None:
            return False
        try:
            sig = inspect.signature(method)
            return len(sig.parameters) >= 3
        except (ValueError, TypeError):
            return True  # assume yes if we cannot inspect

    def validate_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run preflight validation on a tool call without executing it.

        Calls ``executor.validate()`` and converts the ``PreflightResult``
        into a plain dict.

        Returns:
            A dict with ``valid``, ``checks``, and ``requires_approval`` keys.
        """
        try:
            result = self._executor.validate(tool_name, arguments)
            return {
                "valid": result.valid,
                "checks": [
                    {
                        "check": c.check,
                        "passed": c.passed,
                        "error": c.error,
                        "warnings": list(c.warnings),
                    }
                    for c in result.checks
                ],
                "requires_approval": result.requires_approval,
            }
        except Exception as e:
            return {
                "valid": False,
                "checks": [
                    {
                        "check": "unexpected",
                        "passed": False,
                        "error": {"message": str(e)},
                        "warnings": [],
                    }
                ],
                "requires_approval": False,
            }

    async def handle_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Execute a tool call through the Executor pipeline.

        Args:
            tool_name: The MCP tool name (already denormalized to apcore
                module ID by the caller, or passed through as-is).
            arguments: The tool call arguments dict.
            extra: Optional dict with ``progress_token``,
                ``send_notification``, and ``session`` for streaming
                and elicitation support.

        Returns:
            A ``(content, is_error, trace_id)`` tuple where *content* is
            a list of ``TextContent``-compatible dicts, *is_error* signals
            whether the result represents an error, and *trace_id* is the
            execution trace ID (or None).
        """
        logger.debug("Executing tool call: %s", tool_name)

        # Extract streaming helpers from extra
        progress_token: str | int | None = None
        send_notification: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None
        session: Any = None
        if extra is not None:
            progress_token = extra.get("progress_token")
            send_notification = extra.get("send_notification")
            session = extra.get("session")

        # ── Build context with MCP callbacks ─────────────────────────────
        context_data: dict[str, Any] = {}

        # Inject progress callback if progress_token + send_notification available
        if progress_token is not None and send_notification is not None:
            _pt = progress_token
            _sn = send_notification

            async def _progress_callback(
                progress: float,
                total: float | None = None,
                message: str | None = None,
            ) -> None:
                notification: dict[str, Any] = {
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": _pt,
                        "progress": progress,
                        "total": total if total is not None else 0,
                    },
                }
                if message is not None:
                    notification["params"]["message"] = message
                await _sn(notification)

            context_data[MCP_PROGRESS_KEY] = _progress_callback

        # Inject elicitation callback if session available
        if session is not None:
            _session = session

            async def _elicit_callback(
                message: str,
                requested_schema: dict[str, Any] | None = None,
            ) -> dict[str, Any] | None:
                try:
                    result = await _session.elicit_form(
                        message=message,
                        requestedSchema=requested_schema or {},
                    )
                    return {
                        "action": result.action,
                        "content": result.content,
                    }
                except Exception:
                    logger.debug("Elicitation request failed", exc_info=True)
                    return None

            context_data[MCP_ELICIT_KEY] = _elicit_callback

        identity = extra.get("identity") if extra is not None else None

        # Inbound W3C Trace Context: parse `_meta.traceparent` (per MCP _meta
        # passthrough) and seed the apcore Context so downstream modules
        # inherit the trace chain. Context.create() in apcore 0.19 handles
        # strict validation (all-zero/all-f → regen with WARN), so we do not
        # duplicate validation here.
        trace_parent: TraceParent | None = None
        if extra is not None:
            meta = extra.get("_meta")
            if isinstance(meta, dict):
                raw_tp = meta.get("traceparent")
                if isinstance(raw_tp, str):
                    trace_parent = TraceContext.extract({"traceparent": raw_tp})

        context = Context.create(data=context_data, identity=identity, trace_parent=trace_parent)

        version_hint: str | None = None
        if extra is not None:
            version_hint = extra.get("version_hint")
            if version_hint is None:
                meta = extra.get("_meta")
                if isinstance(meta, dict):
                    apcore_meta = meta.get("apcore")
                    if isinstance(apcore_meta, dict):
                        raw = apcore_meta.get("version")
                        if isinstance(raw, str):
                            version_hint = raw

        # Pre-execution validation
        if self._validate_inputs:
            try:
                validation = self._executor.validate(tool_name, arguments, context)
                if not validation.valid:
                    parts: list[str] = []
                    for e in validation.errors:
                        if "errors" in e:
                            for sub in e["errors"]:
                                parts.append(f"{sub.get('field', '?')}: {sub.get('message', 'invalid')}")
                        elif "field" in e:
                            parts.append(f"{e['field']}: {e.get('message', 'invalid')}")
                        else:
                            parts.append(e.get("message", e.get("code", "invalid")))
                    detail = "; ".join(parts)
                    return (
                        [{"type": "text", "text": f"Validation failed: {detail}"}],
                        True,
                        None,
                    )
            except AttributeError:
                pass  # executor lacks validate() — skip
            except Exception as error:
                logger.debug("validate_inputs error for %s: %s", tool_name, error)
                error_info = self._error_mapper.to_mcp_error(error)
                return ([{"type": "text", "text": error_info["message"]}], True, None)

        # Streaming path: executor has stream() AND we have both helpers
        can_stream = hasattr(self._executor, "stream") and progress_token is not None and send_notification is not None

        if can_stream:
            return await self._handle_stream(
                tool_name,
                arguments,
                progress_token,  # type: ignore[arg-type]
                send_notification,  # type: ignore[arg-type]
                context=context,
                version_hint=version_hint,
            )

        # Non-streaming path
        return await self._handle_call_async(tool_name, arguments, context=context, version_hint=version_hint)

    @staticmethod
    def _attach_traceparent(content: list[dict[str, Any]], context: Any | None) -> None:
        """Attach `_meta.traceparent` to the first content item for W3C trace propagation.

        MCP clients can correlate traces across module boundaries. Failures
        (malformed context, empty trace_id) are swallowed so response
        delivery is never blocked by trace metadata.
        """
        if context is None or not content:
            return
        try:
            headers = TraceContext.inject(context)
        except Exception:
            return
        tp = headers.get("traceparent")
        if not tp:
            return
        meta = content[0].get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["traceparent"] = tp
        content[0]["_meta"] = meta

    @staticmethod
    def _build_error_text(error_info: dict[str, Any]) -> str:
        """Build error text content, appending AI guidance fields as structured JSON when present.

        Guidance keys use camelCase to match MCP convention and TypeScript output.
        """
        text = error_info["message"]
        guidance = {
            k: error_info[k] for k in ("retryable", "aiGuidance", "userFixable", "suggestion") if k in error_info
        }
        if guidance:
            text += "\n\n" + json.dumps(guidance)
        return text

    async def _handle_call_async(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: Any | None = None,
        version_hint: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Non-streaming execution via executor.call_async()."""
        try:
            if self._trace:
                # TODO(apcore>=0.19): plumb version_hint through call_async_with_trace
                # (current signature only accepts strategy).
                if self._call_async_accepts_context:
                    result, pipeline_trace = await self._executor.call_async_with_trace(tool_name, arguments, context)
                else:
                    result, pipeline_trace = await self._executor.call_async_with_trace(tool_name, arguments)
                trace_dict = {
                    "strategy_name": pipeline_trace.strategy_name,
                    "total_duration_ms": pipeline_trace.total_duration_ms,
                    "steps": [
                        {
                            "name": s.name,
                            "duration_ms": s.duration_ms,
                            "skipped": s.skipped,
                            "skip_reason": getattr(s, "skip_reason", None),
                        }
                        for s in pipeline_trace.steps
                    ],
                }
            else:
                call_kwargs: dict[str, Any] = {}
                if version_hint is not None:
                    call_kwargs["version_hint"] = version_hint
                if self._call_async_accepts_context:
                    result = await self._executor.call_async(tool_name, arguments, context, **call_kwargs)
                else:
                    result = await self._executor.call_async(tool_name, arguments, **call_kwargs)
                trace_dict = None
            result = self._maybe_redact(tool_name, result)
            text_output = self._format_result(result)
            content: list[dict[str, Any]] = [{"type": "text", "text": text_output}]
            self._attach_traceparent(content, context)
            if trace_dict is not None:
                content.append({"type": "text", "text": json.dumps(trace_dict, default=str)})
            trace_id = context.trace_id if context is not None else None
            return (content, False, trace_id)
        except Exception as error:
            logger.error("handle_call error for %s: %s", tool_name, error)
            error_info = self._error_mapper.to_mcp_error(error)
            return ([{"type": "text", "text": self._build_error_text(error_info)}], True, None)

    async def _handle_stream(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        progress_token: str | int,
        send_notification: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
        context: Any | None = None,
        version_hint: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Streaming execution via executor.stream().

        Iterates the async generator, sends each chunk as a
        ``notifications/progress`` message, accumulates via shallow
        merge, and returns the final accumulated result.
        """
        # TODO(apcore>=0.19): streaming traces pending — Executor.stream()
        # currently yields dict chunks only; no stream_with_trace API exists,
        # so per-step pipeline traces cannot be attached to the stream result.
        accumulated: dict[str, Any] = {}
        chunk_index = 0

        try:
            stream_kwargs: dict[str, Any] = {}
            if version_hint is not None:
                stream_kwargs["version_hint"] = version_hint
            if self._stream_accepts_context:
                stream_iter = self._executor.stream(tool_name, arguments, context, **stream_kwargs)
            else:
                stream_iter = self._executor.stream(tool_name, arguments, **stream_kwargs)

            async for chunk in stream_iter:
                # Send progress notification for this chunk
                notification: dict[str, Any] = {
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": progress_token,
                        "progress": chunk_index + 1,
                        "total": None,
                        "message": json.dumps(chunk, default=str),
                    },
                }
                await send_notification(notification)

                # Deep merge into accumulated result (depth-capped at 32)
                accumulated = _deep_merge(accumulated, chunk)
                chunk_index += 1

            accumulated = self._maybe_redact(tool_name, accumulated)
            text_output = self._format_result(accumulated)
            content: list[dict[str, Any]] = [{"type": "text", "text": text_output}]
            self._attach_traceparent(content, context)
            trace_id = context.trace_id if context is not None else None
            return (content, False, trace_id)
        except Exception as error:
            logger.error("handle_call stream error for %s: %s", tool_name, error)
            error_info = self._error_mapper.to_mcp_error(error)
            return ([{"type": "text", "text": self._build_error_text(error_info)}], True, None)
