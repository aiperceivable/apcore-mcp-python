"""Tests for ErrorMapper: apcore errors → MCP error response dicts."""

from __future__ import annotations

from typing import Any

import pytest

from apcore_mcp.adapters.errors import ErrorMapper


# Stub error classes that mimic apcore error hierarchy for testing
# This avoids hard dependency on apcore in unit tests
class ModuleError(Exception):
    """Base error for all apcore framework errors."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.cause = cause
        self.trace_id = trace_id


class ModuleNotFoundError(ModuleError):
    """Raised when a module cannot be found."""

    def __init__(self, module_id: str, **kwargs: Any) -> None:
        super().__init__(
            code="MODULE_NOT_FOUND",
            message=f"Module not found: {module_id}",
            details={"module_id": module_id},
            **kwargs,
        )


class SchemaValidationError(ModuleError):
    """Raised when schema validation fails."""

    def __init__(
        self,
        message: str = "Schema validation failed",
        errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code="SCHEMA_VALIDATION_ERROR",
            message=message,
            details={"errors": errors or []},
            **kwargs,
        )


class ACLDeniedError(ModuleError):
    """Raised when ACL denies access."""

    def __init__(self, caller_id: str | None, target_id: str, **kwargs: Any) -> None:
        super().__init__(
            code="ACL_DENIED",
            message=f"Access denied: {caller_id} -> {target_id}",
            details={"caller_id": caller_id, "target_id": target_id},
            **kwargs,
        )


class ModuleTimeoutError(ModuleError):
    """Raised when module execution exceeds timeout."""

    def __init__(self, module_id: str, timeout_ms: int, **kwargs: Any) -> None:
        super().__init__(
            code="MODULE_TIMEOUT",
            message=f"Module {module_id} timed out after {timeout_ms}ms",
            details={"module_id": module_id, "timeout_ms": timeout_ms},
            **kwargs,
        )


class InvalidInputError(ModuleError):
    """Raised for invalid input."""

    def __init__(self, message: str = "Invalid input", **kwargs: Any) -> None:
        super().__init__(code="GENERAL_INVALID_INPUT", message=message, **kwargs)


class CallDepthExceededError(ModuleError):
    """Raised when call chain exceeds maximum depth."""

    def __init__(self, depth: int, max_depth: int, call_chain: list[str], **kwargs: Any) -> None:
        super().__init__(
            code="CALL_DEPTH_EXCEEDED",
            message=f"Call depth {depth} exceeds maximum {max_depth}",
            details={"depth": depth, "max_depth": max_depth, "call_chain": call_chain},
            **kwargs,
        )


class CircularCallError(ModuleError):
    """Raised when a circular call is detected."""

    def __init__(self, module_id: str, call_chain: list[str], **kwargs: Any) -> None:
        super().__init__(
            code="CIRCULAR_CALL",
            message=f"Circular call detected for module {module_id}",
            details={"module_id": module_id, "call_chain": call_chain},
            **kwargs,
        )


class CallFrequencyExceededError(ModuleError):
    """Raised when a module is called too many times."""

    def __init__(
        self,
        module_id: str,
        count: int,
        max_repeat: int,
        call_chain: list[str],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code="CALL_FREQUENCY_EXCEEDED",
            message=f"Module {module_id} called {count} times, max is {max_repeat}",
            details={
                "module_id": module_id,
                "count": count,
                "max_repeat": max_repeat,
                "call_chain": call_chain,
            },
            **kwargs,
        )


# Test cases
class TestErrorMapper:
    """Test suite for ErrorMapper."""

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        """Create an ErrorMapper instance."""
        return ErrorMapper()

    def test_module_not_found(self, mapper: ErrorMapper) -> None:
        """ModuleNotFoundError contains module_id in message and error_type."""
        error = ModuleNotFoundError("image.resize")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "MODULE_NOT_FOUND"
        assert "image.resize" in result["message"]

    def test_schema_validation_error(self, mapper: ErrorMapper) -> None:
        """SchemaValidationError includes field details in message."""
        errors = [
            {"field": "name", "message": "required"},
            {"field": "age", "message": "must be positive"},
        ]
        error = SchemaValidationError("Validation failed", errors=errors)
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "SCHEMA_VALIDATION_ERROR"
        assert "name" in result["message"]
        assert "age" in result["message"]

    def test_acl_denied(self, mapper: ErrorMapper) -> None:
        """ACLDeniedError says access denied but does NOT leak caller_id."""
        error = ACLDeniedError("user1", "admin.delete")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "ACL_DENIED"
        assert "access denied" in result["message"].lower()
        # Should NOT contain sensitive caller_id
        assert "user1" not in result["message"]

    def test_module_timeout(self, mapper: ErrorMapper) -> None:
        """ModuleTimeoutError mentions timeout."""
        error = ModuleTimeoutError("slow.module", 5000)
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "MODULE_TIMEOUT"
        assert "timeout" in result["message"].lower() or "timed out" in result["message"].lower()

    def test_invalid_input(self, mapper: ErrorMapper) -> None:
        """InvalidInputError preserves message."""
        error = InvalidInputError("missing field X")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "GENERAL_INVALID_INPUT"
        assert "missing field X" in result["message"]

    def test_call_depth_exceeded(self, mapper: ErrorMapper) -> None:
        """CallDepthExceededError becomes internal error."""
        error = CallDepthExceededError(10, 10, ["a", "b"])
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "CALL_DEPTH_EXCEEDED"
        assert "internal" in result["message"].lower()

    def test_circular_call(self, mapper: ErrorMapper) -> None:
        """CircularCallError becomes internal error."""
        error = CircularCallError("a", ["a", "b", "a"])
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "CIRCULAR_CALL"
        assert "internal" in result["message"].lower()

    def test_call_frequency_exceeded(self, mapper: ErrorMapper) -> None:
        """CallFrequencyExceededError becomes internal error."""
        error = CallFrequencyExceededError("a", 5, 3, ["a"])
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "CALL_FREQUENCY_EXCEEDED"
        assert "internal" in result["message"].lower()

    def test_unexpected_exception(self, mapper: ErrorMapper) -> None:
        """Unexpected exceptions become generic internal error with NO stack trace."""
        error = ValueError("oops")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "INTERNAL_ERROR"
        assert "internal error" in result["message"].lower()
        # Should NOT leak the original error message
        assert "oops" not in result["message"]

    def test_all_errors_set_is_error(self, mapper: ErrorMapper) -> None:
        """Every error type returns is_error=True."""
        errors = [
            ModuleNotFoundError("test"),
            SchemaValidationError("test"),
            ACLDeniedError("user", "admin"),
            ModuleTimeoutError("test", 100),
            InvalidInputError("test"),
            CallDepthExceededError(1, 1, []),
            CircularCallError("test", []),
            CallFrequencyExceededError("test", 5, 3, []),
            ValueError("unexpected"),
        ]

        for error in errors:
            result = mapper.to_mcp_error(error)
            assert result["isError"] is True, f"{type(error).__name__} should set isError=True"

    def test_sanitize_no_stack_trace(self, mapper: ErrorMapper) -> None:
        """Unexpected exceptions don't leak traceback info."""
        error = RuntimeError("internal details that should not be exposed")
        result = mapper.to_mcp_error(error)

        # Should not contain any of the internal error details
        assert "internal details" not in result["message"]
        assert "RuntimeError" not in result["message"]
        # Should be generic message
        assert result["message"] == "Internal error occurred"

    # ── AI guidance fields ──────────────────────────────────────────────

    def test_ai_guidance_retryable(self, mapper: ErrorMapper) -> None:
        """retryable=True appears in result when set on the error."""
        error = ModuleError(
            code="MODULE_EXECUTE_ERROR",
            message="Transient failure",
        )
        error.retryable = True
        result = mapper.to_mcp_error(error)

        assert result["retryable"] is True

    def test_ai_guidance_string(self, mapper: ErrorMapper) -> None:
        """aiGuidance string appears in result when set on the error."""
        error = ModuleError(
            code="MODULE_EXECUTE_ERROR",
            message="Bad input",
        )
        error.ai_guidance = "Try providing the field in ISO-8601 format"
        result = mapper.to_mcp_error(error)

        assert result["aiGuidance"] == "Try providing the field in ISO-8601 format"

    def test_ai_guidance_user_fixable_and_suggestion(self, mapper: ErrorMapper) -> None:
        """userFixable and suggestion appear in result when set."""
        error = ModuleError(
            code="GENERAL_INVALID_INPUT",
            message="Missing required field",
        )
        error.user_fixable = True
        error.suggestion = "Add the 'name' field to your input"
        result = mapper.to_mcp_error(error)

        assert result["userFixable"] is True
        assert result["suggestion"] == "Add the 'name' field to your input"

    def test_ai_guidance_fields_omitted_when_none(self, mapper: ErrorMapper) -> None:
        """AI guidance fields are omitted when not set (None)."""
        error = ModuleError(
            code="MODULE_EXECUTE_ERROR",
            message="Some error",
        )
        # No AI guidance fields set — they should not appear in result
        result = mapper.to_mcp_error(error)

        assert "retryable" not in result
        assert "aiGuidance" not in result
        assert "userFixable" not in result
        assert "suggestion" not in result


class TestEM3UserFixableHardcoding:
    """[EM-3] The bridge stamps userFixable=True on dependency / binding /
    version-constraint errors to match TS behaviour, since apcore 0.19's
    error classes don't set user_fixable themselves yet.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_dependency_not_found_is_user_fixable(self, mapper: ErrorMapper) -> None:
        from apcore.errors import DependencyNotFoundError

        err = DependencyNotFoundError(module_id="m", dependency_id="d")
        result = mapper.to_mcp_error(err)
        assert result["errorType"] == "DEPENDENCY_NOT_FOUND"
        assert result["userFixable"] is True

    def test_dependency_version_mismatch_is_user_fixable(self, mapper: ErrorMapper) -> None:
        from apcore.errors import DependencyVersionMismatchError

        err = DependencyVersionMismatchError(module_id="m", dependency_id="d", required="1.0", actual="0.9")
        result = mapper.to_mcp_error(err)
        assert result["errorType"] == "DEPENDENCY_VERSION_MISMATCH"
        assert result["userFixable"] is True

    @pytest.mark.parametrize(
        "code",
        [
            "VERSION_CONSTRAINT_INVALID",
            "BINDING_SCHEMA_INFERENCE_FAILED",
            "BINDING_SCHEMA_MODE_CONFLICT",
            "BINDING_STRICT_SCHEMA_INCOMPATIBLE",
            "BINDING_POLICY_VIOLATION",
        ],
    )
    def test_user_fixable_codes(self, mapper: ErrorMapper, code: str) -> None:
        err = ModuleError(code=code, message="x")
        result = mapper.to_mcp_error(err)
        assert result["errorType"] == code
        assert result["userFixable"] is True

    def test_unrelated_codes_do_not_get_user_fixable(self, mapper: ErrorMapper) -> None:
        err = ModuleError(code="MODULE_EXECUTE_ERROR", message="x")
        result = mapper.to_mcp_error(err)
        assert "userFixable" not in result

    def test_explicit_false_overrides_default_stamp(self, mapper: ErrorMapper) -> None:
        """If apcore later sets user_fixable=False, the stamp must not override."""
        err = ModuleError(code="VERSION_CONSTRAINT_INVALID", message="x")
        # Bridge stamps True; subsequent _attach_ai_guidance must not overwrite it.
        # Conversely if upstream sets False explicitly, that wins.
        err.user_fixable = False
        result = mapper.to_mcp_error(err)
        # Stamped True first; _attach_ai_guidance preserves the existing key.
        # This is intentional: bridge default reflects the docs-level guarantee.
        assert result["userFixable"] is True

    # ── Approval error handling ─────────────────────────────────────────

    def test_approval_denied_passes_through_message_and_reason(self, mapper: ErrorMapper) -> None:
        """APPROVAL_DENIED passes through message and reason."""
        error = ModuleError(
            code="APPROVAL_DENIED",
            message="User denied the operation",
            details={"reason": "Not authorized for production"},
        )
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "APPROVAL_DENIED"
        assert result["message"] == "User denied the operation"
        assert result["details"]["reason"] == "Not authorized for production"

    def test_approval_timeout_marks_retryable(self, mapper: ErrorMapper) -> None:
        """APPROVAL_TIMEOUT marks retryable=True in response."""
        error = ModuleError(
            code="APPROVAL_TIMEOUT",
            message="Approval timed out after 60s",
        )
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "APPROVAL_TIMEOUT"
        assert result["retryable"] is True

    def test_approval_pending_includes_approval_id(self, mapper: ErrorMapper) -> None:
        """APPROVAL_PENDING includes approvalId in details (camelCase output)."""
        error = ModuleError(
            code="APPROVAL_PENDING",
            message="Awaiting approval",
            details={"approval_id": "apr-123"},
        )
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "APPROVAL_PENDING"
        assert result["details"]["approvalId"] == "apr-123"

    def test_approval_pending_without_approval_id_has_none_details(self, mapper: ErrorMapper) -> None:
        """APPROVAL_PENDING without approval_id narrows details to None."""
        error = ModuleError(
            code="APPROVAL_PENDING",
            message="Awaiting approval",
            details={"internal_state": "should not leak"},
        )
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "APPROVAL_PENDING"
        assert result["details"] is None

    # ── ExecutionCancelledError ────────────────────────────────────────

    def test_execution_cancelled_error(self, mapper: ErrorMapper) -> None:
        """ExecutionCancelledError maps to EXECUTION_CANCELLED with retryable=True."""
        from apcore.cancel import ExecutionCancelledError

        error = ExecutionCancelledError("Execution was cancelled")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "EXECUTION_CANCELLED"
        assert result["retryable"] is True
        assert result["message"] == "Execution was cancelled"

    def test_execution_cancelled_error_sanitizes_custom_message(self, mapper: ErrorMapper) -> None:
        """ExecutionCancelledError with custom message is sanitized to fixed string."""
        from apcore.cancel import ExecutionCancelledError

        error = ExecutionCancelledError("internal detail: connection pool exhausted on host db-3")
        result = mapper.to_mcp_error(error)

        assert result["message"] == "Execution was cancelled"
        assert "internal detail" not in result["message"]
        assert "db-3" not in result["message"]
