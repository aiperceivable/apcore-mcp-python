"""ErrorMapper: apcore error hierarchy → MCP error responses."""

from __future__ import annotations

from typing import Any

from apcore.cancel import ExecutionCancelledError
from apcore.errors import (
    DependencyNotFoundError,
    DependencyVersionMismatchError,
    ModuleError,
    TaskLimitExceededError,
)

from apcore_mcp.constants import ERROR_CODES


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

    def to_mcp_error(self, error: Exception) -> dict[str, Any]:
        """
        Convert any exception to an MCP error response dict.

        Returns:
            dict with keys:
                - is_error: True
                - error_type: str (error code or "INTERNAL_ERROR")
                - message: str (safe error message)
                - details: dict | None (optional additional context)
        """
        # ExecutionCancelledError is not a ModuleError subclass
        if isinstance(error, ExecutionCancelledError):
            return {
                "is_error": True,
                "error_type": ERROR_CODES["EXECUTION_CANCELLED"],
                "message": "Execution was cancelled",
                "details": None,
                "retryable": True,
            }

        # Prefer isinstance dispatch for apcore 0.19 error classes so cross-language
        # error propagation stays stable even if `.code` drift occurs upstream.
        if isinstance(error, TaskLimitExceededError):
            return {
                "is_error": True,
                "error_type": ERROR_CODES["TASK_LIMIT_EXCEEDED"],
                "message": getattr(error, "message", str(error)),
                "details": getattr(error, "details", None),
                "retryable": True,
            }
        if isinstance(error, DependencyNotFoundError):
            return {
                "is_error": True,
                "error_type": ERROR_CODES["DEPENDENCY_NOT_FOUND"],
                "message": getattr(error, "message", str(error)),
                "details": getattr(error, "details", None),
            }
        if isinstance(error, DependencyVersionMismatchError):
            return {
                "is_error": True,
                "error_type": ERROR_CODES["DEPENDENCY_VERSION_MISMATCH"],
                "message": getattr(error, "message", str(error)),
                "details": getattr(error, "details", None),
            }

        # Check if it's an apcore ModuleError by isinstance (fast path for
        # known hierarchy) or structural duck-typing (fallback for compatibility).
        if isinstance(error, ModuleError) or (
            hasattr(error, "code") and hasattr(error, "message") and hasattr(error, "details")
        ):
            return self._handle_apcore_error(error)

        # Unknown exception - sanitize completely
        return {
            "is_error": True,
            "error_type": ERROR_CODES["INTERNAL_ERROR"],
            "message": "Internal error occurred",
            "details": None,
        }

    # Map apcore ModuleError attribute names (snake_case) to MCP wire format (camelCase).
    # The wire format uses camelCase to match MCP convention and TypeScript output.
    # apcore input: error.ai_guidance → MCP output: result["aiGuidance"]
    _AI_GUIDANCE_FIELDS: dict[str, str] = {
        "retryable": "retryable",
        "ai_guidance": "aiGuidance",
        "user_fixable": "userFixable",
        "suggestion": "suggestion",
    }

    def _handle_apcore_error(self, error: Exception) -> dict[str, Any]:
        """Handle known apcore errors."""
        code: str = getattr(error, "code", "UNKNOWN")
        message: str = getattr(error, "message", str(error))
        raw_details: Any = getattr(error, "details", None)
        details: dict[str, Any] | None = raw_details if raw_details is not None else None

        # Convert internal errors to generic message
        if code in self._INTERNAL_ERROR_CODES:
            return {
                "is_error": True,
                "error_type": code,
                "message": "Internal error occurred",
                "details": None,
            }

        # Sanitize ACL errors to not leak caller information
        if code in self._SANITIZED_ERROR_CODES:
            return {
                "is_error": True,
                "error_type": code,
                "message": "Access denied",
                "details": None,
            }

        # Schema validation errors need special formatting
        if code == ERROR_CODES["SCHEMA_VALIDATION_ERROR"] and details is not None:
            formatted_message = self._format_validation_errors(details.get("errors", []))
            result: dict[str, Any] = {
                "is_error": True,
                "error_type": code,
                "message": formatted_message if formatted_message else message,
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
                "is_error": True,
                "error_type": code,
                "message": message,
                "details": narrowed,
            }
            self._attach_ai_guidance(error, result)
            return result

        if code == ERROR_CODES["APPROVAL_TIMEOUT"]:
            result = {
                "is_error": True,
                "error_type": code,
                "message": message,
                "details": details,
                "retryable": True,
            }
            self._attach_ai_guidance(error, result)
            return result

        if code == ERROR_CODES["APPROVAL_DENIED"]:
            reason = details.get("reason") if details else None
            result = {
                "is_error": True,
                "error_type": code,
                "message": message,
                "details": {"reason": reason} if reason else details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Config env map conflict
        if code == ERROR_CODES.get("CONFIG_ENV_MAP_CONFLICT"):
            env_var = details.get("env_var", "unknown") if details else "unknown"
            result = {
                "is_error": True,
                "error_type": code,
                "message": f"Config env map conflict: {env_var}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Pipeline abort
        if code == ERROR_CODES.get("PIPELINE_ABORT") or type(error).__name__ == "PipelineAbortError":
            step = details.get("step", "unknown") if details else "unknown"
            result = {
                "is_error": True,
                "error_type": code,
                "message": f"Pipeline aborted at step: {step}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Step not found
        if code == ERROR_CODES.get("STEP_NOT_FOUND"):
            result = {
                "is_error": True,
                "error_type": code,
                "message": f"Pipeline step not found: {message}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # Version incompatible
        if code == ERROR_CODES.get("VERSION_INCOMPATIBLE"):
            result = {
                "is_error": True,
                "error_type": code,
                "message": f"Version incompatible: {message}",
                "details": details,
            }
            self._attach_ai_guidance(error, result)
            return result

        # apcore 0.19.0: TaskLimitExceededError is retryable per changelog.
        if code == ERROR_CODES.get("TASK_LIMIT_EXCEEDED"):
            result = {
                "is_error": True,
                "error_type": code,
                "message": message,
                "details": details,
                "retryable": True,
            }
            self._attach_ai_guidance(error, result)
            return result

        # All other apcore errors: pass through message and details
        result = {
            "is_error": True,
            "error_type": code,
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

    def _format_validation_errors(self, errors: list[dict[str, Any]]) -> str:
        """Format SchemaValidationError field-level errors into readable message."""
        if not errors:
            return "Schema validation failed"

        # Format each error as "field: message"
        error_lines = []
        for err in errors:
            field = err.get("field", "unknown")
            msg = err.get("message", "invalid")
            error_lines.append(f"{field}: {msg}")

        return "Schema validation failed:\n" + "\n".join(f"  {line}" for line in error_lines)
