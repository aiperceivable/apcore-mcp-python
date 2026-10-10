"""ErrorMapper: apcore error hierarchy → MCP error responses."""

from __future__ import annotations

from typing import Any

from apcore.cancel import ExecutionCancelledError
from apcore.errors import (
    ModuleError,
    TaskLimitExceededError,
)

# apcore 0.20.0 introduced ``CircuitBreakerOpenError`` as the canonical
# replacement for the legacy ``CircuitOpenError`` (sync alignment A-001).
# Both classes inherit from ``ModuleError``; we do best-effort import so
# pre-0.20 builds still work — the ``isinstance`` branch below is skipped
# when the symbol is absent.
try:  # pragma: no cover - import shim
    from apcore.errors import CircuitBreakerOpenError  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    CircuitBreakerOpenError = None  # type: ignore[assignment,misc]

from apcore_mcp.constants import ERROR_CODES

# Canonical recovery hint emitted when apcore did not populate a per-module
# ``ai_guidance`` on a circuit-breaker rejection. Kept byte-identical with the
# TypeScript and Rust SDKs.
CIRCUIT_BREAKER_RECOVERY_GUIDANCE = (
    "Module's circuit breaker is OPEN — repeated failures have tripped "
    "the breaker. Wait until the recovery window elapses, then retry; "
    "the breaker will move to HALF_OPEN and accept a trial call."
)


class ErrorMapper:
    """Maps apcore exceptions to MCP error response dictionaries."""

    # Error codes that should be treated as internal errors
    _INTERNAL_ERROR_CODES = {
        ERROR_CODES["CALL_DEPTH_EXCEEDED"],
        ERROR_CODES["CIRCULAR_CALL"],
        ERROR_CODES["CALL_FREQUENCY_EXCEEDED"],
    }

    # Error codes that require sanitization (hide sensitive details)
    _SANITIZED_ERROR_CODES = {
        ERROR_CODES["ACL_DENIED"],
    }

    def format(self, error: Exception, context: object = None) -> dict[str, Any]:
        """Format an apcore error into an MCP error response.

        Implements the ErrorFormatter protocol so ``ErrorMapper`` can be used
        directly with ``ErrorFormatterRegistry.register("mcp", ErrorMapper())``,
        eliminating the need for the ``MCPErrorFormatter`` passthrough wrapper.

        Args:
            error: The exception to format.
            context: Optional context object (ignored; kept for protocol compatibility).

        Returns:
            MCP error response dict (same as ``to_mcp_error``).
        """
        return self.to_mcp_error(error)

    def to_mcp_error(self, error: Exception) -> dict[str, Any]:
        """
        Convert any exception to an MCP error response dict.

        Returns:
            dict with keys:
                - isError: True
                - errorType: str (error code or "INTERNAL_ERROR")
                - message: str (safe error message)
                - details: dict | None (optional additional context)
        """
        # ExecutionCancelledError is not a ModuleError subclass
        if isinstance(error, ExecutionCancelledError):
            return {
                "isError": True,
                "errorType": ERROR_CODES["EXECUTION_CANCELLED"],
                "message": "Execution was cancelled",
                "details": None,
                "retryable": True,
            }

        # Prefer isinstance dispatch for apcore 0.19 error classes so cross-language
        # error propagation stays stable even if `.code` drift occurs upstream.
        if isinstance(error, TaskLimitExceededError):
            return self._task_limit_response(error)
        # apcore 0.20.0: surface circuit-breaker rejections with a
        # retryable=true envelope and an AI-facing recovery hint so the
        # orchestrator backs off until the recovery window elapses. The
        # error class already populates ``ai_guidance`` with the per-module
        # recovery message; ``_attach_ai_guidance`` mirrors it onto the
        # MCP envelope under the canonical ``aiGuidance`` key.
        if CircuitBreakerOpenError is not None and isinstance(error, CircuitBreakerOpenError):
            return self._circuit_breaker_response(error)
        # Check if it's an apcore ModuleError by isinstance (fast path for
        # known hierarchy) or structural duck-typing (fallback for compatibility).
        if isinstance(error, ModuleError) or (
            hasattr(error, "code") and hasattr(error, "message") and hasattr(error, "details")
        ):
            return self._handle_apcore_error(error)

        # Unknown exception - sanitize completely.
        # Emits GENERAL_INTERNAL_ERROR (not INTERNAL_ERROR) per spec EM-6 so MCP
        # clients can branch on errorType === "GENERAL_INTERNAL_ERROR" portably.
        return internal_error_response()

    def to_mcp_error_any(self, error: Any) -> dict[str, Any]:
        """Generic-error fallback for arbitrary inputs.

        [D9-003] Mirrors Rust's downcast pattern: when ``error`` is an apcore
        ``ModuleError`` (or one of the typed subclasses handled by
        ``to_mcp_error``), delegate to the typed path so the caller still
        gets ``aiGuidance`` / ``userFixable`` / ``retryable`` enrichment.
        Only true foreign exceptions fall through to the canonical
        ``GENERAL_INTERNAL_ERROR`` envelope — the original class name,
        message, traceback, and details are deliberately discarded to avoid
        leaking server-side state.
        """
        # Forward known apcore error types (incl. ExecutionCancelledError,
        # TaskLimitExceededError, CircuitBreakerOpenError, and every ModuleError subclass).
        if isinstance(error, ModuleError | ExecutionCancelledError):
            return self.to_mcp_error(error)
        return internal_error_response()

    # Map apcore ModuleError attribute names (snake_case) to MCP wire format (camelCase).
    # The wire format uses camelCase to match MCP convention and TypeScript output.
    # apcore input: error.ai_guidance → MCP output: result["aiGuidance"]
    _AI_GUIDANCE_FIELDS: dict[str, str] = {
        "retryable": "retryable",
        "ai_guidance": "aiGuidance",
        "user_fixable": "userFixable",
        "suggestion": "suggestion",
    }

    def _task_limit_response(self, error: Exception) -> dict[str, Any]:
        """Canonical TASK_LIMIT_EXCEEDED envelope (retryable + AI guidance)."""
        result: dict[str, Any] = {
            "isError": True,
            "errorType": ERROR_CODES["TASK_LIMIT_EXCEEDED"],
            "message": getattr(error, "message", str(error)),
            "details": getattr(error, "details", None),
            "retryable": True,
        }
        self._attach_ai_guidance(error, result)
        return result

    def _circuit_breaker_response(self, error: Exception) -> dict[str, Any]:
        """Canonical CIRCUIT_BREAKER_OPEN envelope (retryable + AI guidance)."""
        result: dict[str, Any] = {
            "isError": True,
            "errorType": ERROR_CODES["CIRCUIT_BREAKER_OPEN"],
            "message": getattr(error, "message", str(error)),
            "details": getattr(error, "details", None),
            "retryable": True,
        }
        self._attach_ai_guidance(error, result)
        # [D10-003] Mirror TS+Rust: when apcore did not populate a
        # per-module ai_guidance, fall back to the canonical recovery
        # hint so cross-language envelopes stay byte-identical.
        result.setdefault("aiGuidance", CIRCUIT_BREAKER_RECOVERY_GUIDANCE)
        return result

    def _handle_apcore_error(self, error: Exception) -> dict[str, Any]:
        """Handle known apcore errors."""
        code: str = getattr(error, "code", "UNKNOWN")
        message: str = getattr(error, "message", str(error))
        raw_details: Any = getattr(error, "details", None)
        # A duck-typed or wire-reconstructed ModuleError may carry a non-mapping
        # ``details`` (string, list, scalar). Every lookup below assumes a
        # mapping and ``to_mcp_error`` must never raise, so narrow to the
        # declared type once here instead of guarding each call site.
        details: dict[str, Any] | None = raw_details if isinstance(raw_details, dict) else None

        # Convert internal errors to generic message
        if code in self._INTERNAL_ERROR_CODES:
            return {
                "isError": True,
                "errorType": code,
                "message": "Internal error occurred",
                "details": None,
            }

        # Sanitize ACL errors to not leak caller information
        if code in self._SANITIZED_ERROR_CODES:
            return {
                "isError": True,
                "errorType": code,
                "message": "Access denied",
                "details": None,
            }

        # [A-D-EM-3] Code-based dispatch for the two classes that also have an
        # isinstance fast path above. A duck-typed or wire-reconstructed
        # ModuleError carrying these codes never reaches the isinstance branch
        # (and CircuitBreakerOpenError is absent entirely on apcore < 0.20), so
        # without these rows it would fall through to the plain passthrough and
        # lose ``retryable`` / ``aiGuidance``. Mirrors errors.ts:253 / :268.
        if code == ERROR_CODES["TASK_LIMIT_EXCEEDED"]:
            return self._task_limit_response(error)

        if code == ERROR_CODES["CIRCUIT_BREAKER_OPEN"]:
            return self._circuit_breaker_response(error)

        # Schema validation errors need special formatting.
        # Parity with TS+Rust: always emit the canonical formatted message — never
        # passthrough error.message (which could leak server-side state). When
        # details is None/empty, _format_validation_errors([]) returns the canonical
        # "Schema validation failed".
        if code == ERROR_CODES["SCHEMA_VALIDATION_ERROR"]:
            errors_list = details.get("errors", []) if details else []
            formatted_message = self._format_validation_errors(errors_list)
            result: dict[str, Any] = {
                "isError": True,
                "errorType": code,
                "message": formatted_message,
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Approval errors: pass through with specific handling
        if code == ERROR_CODES["APPROVAL_PENDING"]:
            # Narrow details to only approvalId; drop everything else.
            # apcore uses snake_case (approval_id); output uses camelCase (approvalId) for MCP convention.
            narrowed = {"approvalId": details["approval_id"]} if details and "approval_id" in details else None
            result = {
                "isError": True,
                "errorType": code,
                "message": message,
                "details": narrowed,
            }
            self._attach_ai_guidance(error, result)
            return result

        if code == ERROR_CODES["APPROVAL_TIMEOUT"]:
            result = {
                "isError": True,
                "errorType": code,
                "message": message,
                "details": details,
                "retryable": True,
            }
            self._attach_ai_guidance(error, result)
            return result

        if code == ERROR_CODES["APPROVAL_DENIED"]:
            reason = details.get("reason") if details else None
            result = {
                "isError": True,
                "errorType": code,
                "message": message,
                "details": {"reason": reason} if reason else details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Config env map conflict
        if code == ERROR_CODES.get("CONFIG_ENV_MAP_CONFLICT"):
            env_var = details.get("env_var", "unknown") if details else "unknown"
            result = {
                "isError": True,
                "errorType": code,
                "message": f"Config env map conflict: {env_var}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Pipeline abort
        if code == ERROR_CODES.get("PIPELINE_ABORT") or type(error).__name__ == "PipelineAbortError":
            step = details.get("step", "unknown") if details else "unknown"
            result = {
                "isError": True,
                "errorType": code,
                "message": f"Pipeline aborted at step: {step}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Step not found
        if code == ERROR_CODES.get("STEP_NOT_FOUND"):
            result = {
                "isError": True,
                "errorType": code,
                "message": f"Pipeline step not found: {message}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Version incompatible
        if code == ERROR_CODES.get("VERSION_INCOMPATIBLE"):
            result = {
                "isError": True,
                "errorType": code,
                "message": f"Version incompatible: {message}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # All other apcore errors: pass through message and details.
        # user_fixable is resolved by apcore at construction time and flows
        # through via _attach_ai_guidance (no bridge-level stamping needed).
        result = {
            "isError": True,
            "errorType": code,
            "message": message,
            "details": details,
        }
        self._attach_ai_guidance(error, result)
        return result

    def _attach_ai_guidance(self, error: Exception, result: dict[str, Any]) -> None:
        """Extract AI guidance fields from error and attach non-None values to result.

        Reads snake_case attributes from the apcore error and writes camelCase
        keys to the MCP result dict (matching MCP/TypeScript convention).
        """
        for src_field, dest_field in self._AI_GUIDANCE_FIELDS.items():
            value = getattr(error, src_field, None)
            if value is not None and dest_field not in result:
                result[dest_field] = value

    def _format_validation_errors(self, errors: Any) -> str:
        """Format SchemaValidationError field-level errors into readable message.

        ``errors`` comes from ``error.details["errors"]`` and is upstream
        controlled — it may be a string, a scalar, or a list holding non-mapping
        entries. ``to_mcp_error`` never raises, so anything that is not a list of
        dicts degrades to the canonical message. Mirrors TypeScript's
        ``Array.isArray`` guard (errors.ts:447) and Rust's ``Some(Value::Array)``
        match (errors.rs:329).
        """
        if not isinstance(errors, list) or not errors:
            return "Schema validation failed"

        # Prefer JSON Pointer paths; retain the legacy field fallback.
        error_lines = []
        for err in errors:
            if isinstance(err, dict):
                path = err.get("path")
                field = (path or "<root>") if isinstance(path, str) else err.get("field", "unknown")
                msg = err.get("message", "invalid")
            else:
                field, msg = "unknown", str(err)
            error_lines.append(f"{field}: {msg}")

        return "Schema validation failed:\n" + "\n".join(f"  {line}" for line in error_lines)


def internal_error_response() -> dict[str, Any]:
    """Canonical GENERAL_INTERNAL_ERROR envelope for non-ModuleError fallback.

    All three SDKs (Python, TypeScript, Rust) emit byte-identical envelopes
    so MCP clients can branch on `errorType === "GENERAL_INTERNAL_ERROR"`
    portably. See ``apcore-mcp/docs/features/error-mapper.md`` (EM-6).
    """
    return {
        "isError": True,
        "errorType": ERROR_CODES["GENERAL_INTERNAL_ERROR"],
        "message": "Internal error occurred",
        "details": None,
    }
