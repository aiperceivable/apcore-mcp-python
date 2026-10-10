"""Tests for ErrorMapper: apcore errors → MCP error response dicts."""

from __future__ import annotations

from typing import Any

import pytest

from apcore_mcp.adapters.errors import CIRCUIT_BREAKER_RECOVERY_GUIDANCE, ErrorMapper

# Declarative user_fixable policy, mirroring apcore 0.24.0 _USER_FIXABLE_BY_CODE.
_USER_FIXABLE_BY_CODE: dict[str, bool] = {
    "SCHEMA_VALIDATION_ERROR": True,
    "GENERAL_INVALID_INPUT": True,
    "MODULE_NOT_FOUND": True,
    "VERSION_CONSTRAINT_INVALID": True,
    "BINDING_SCHEMA_INFERENCE_FAILED": True,
    "BINDING_SCHEMA_MODE_CONFLICT": True,
    "BINDING_STRICT_SCHEMA_INCOMPATIBLE": True,
    "DEPENDENCY_NOT_FOUND": True,
    "DEPENDENCY_VERSION_MISMATCH": True,
    "ACL_DENIED": False,
    "APPROVAL_DENIED": False,
    "APPROVAL_TIMEOUT": False,
    "MODULE_TIMEOUT": False,
    "MODULE_DISABLED": False,
    "CALL_DEPTH_EXCEEDED": False,
    "CIRCULAR_CALL": False,
    "CALL_FREQUENCY_EXCEEDED": False,
    "GENERAL_INTERNAL_ERROR": False,
}

_UNSET: Any = object()


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
        retryable: Any = _UNSET,
        ai_guidance: str | None = None,
        user_fixable: Any = _UNSET,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.cause = cause
        self.trace_id = trace_id
        self.retryable = None if retryable is _UNSET else retryable
        self.ai_guidance = ai_guidance
        self.user_fixable = _USER_FIXABLE_BY_CODE.get(code) if user_fixable is _UNSET else user_fixable
        self.suggestion = suggestion


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

    @pytest.mark.parametrize(
        ("entry", "location"),
        [
            ({"path": "/items/0/a~1b~0c", "keyword": "type", "message": "invalid"}, "/items/0/a~1b~0c"),
            ({"path": "", "keyword": "required", "message": "invalid"}, "<root>"),
            ({"path": "/", "keyword": "type", "message": "invalid"}, "/"),
            ({"path": None, "field": "legacy", "message": "invalid"}, "legacy"),
            ({"path": 42, "field": "legacy", "message": "invalid"}, "legacy"),
            ({"path": "/name", "field": "legacy", "keyword": "type", "message": "invalid"}, "/name"),
        ],
    )
    def test_schema_validation_json_pointer(self, mapper: ErrorMapper, entry: dict, location: str) -> None:
        """Canonical paths take precedence and retain JSON Pointer escaping."""
        error = SchemaValidationError("Validation failed", errors=[entry])
        result = mapper.to_mcp_error(error)
        assert result["message"] == f"Schema validation failed:\n  {location}: invalid"
        assert result["details"] == {"errors": [entry]}

    def test_schema_validation_error_emits_canonical_message_when_details_none(self, mapper: ErrorMapper) -> None:
        """[D10-001] SCHEMA_VALIDATION_ERROR with details=None must emit canonical message.

        Parity with TS+Rust: regardless of whether `details` is None or empty, the
        canonical "Schema validation failed" message is emitted. The original
        `error.message` must NOT leak through (defense against raw passthrough that
        could expose server-side state).
        """
        error = ModuleError(code="SCHEMA_VALIDATION_ERROR", message="custom raw message")
        error.details = None  # explicit None, bypassing the stub's `details or {}`

        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "SCHEMA_VALIDATION_ERROR"
        assert result["message"] == "Schema validation failed"
        # Raw message must not leak
        assert "custom raw message" not in result["message"]

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
        """Unexpected exceptions become canonical GENERAL_INTERNAL_ERROR envelope (EM-6)."""
        error = ValueError("oops")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "GENERAL_INTERNAL_ERROR"
        assert "internal error" in result["message"].lower()
        # Should NOT leak the original error message
        assert "oops" not in result["message"]
        # Canonical envelope: details is None
        assert result["details"] is None

    def test_internal_error_response_helper_envelope(self) -> None:
        """internal_error_response() returns the canonical GENERAL_INTERNAL_ERROR envelope (EM-6)."""
        from apcore_mcp.adapters.errors import internal_error_response

        envelope = internal_error_response()
        assert envelope == {
            "isError": True,
            "errorType": "GENERAL_INTERNAL_ERROR",
            "message": "Internal error occurred",
            "details": None,
        }

    def test_to_mcp_error_any_ignores_input_contents(self, mapper: ErrorMapper) -> None:
        """ErrorMapper.to_mcp_error_any returns the canonical envelope regardless of input.

        Spec EM-6: the helper deliberately ignores the error's class, message,
        traceback, and details to avoid leaking server-side state. Parity with
        TS/Rust: only the method form on ErrorMapper exists.
        """
        from apcore_mcp.adapters.errors import internal_error_response

        canonical = internal_error_response()
        # Method form on ErrorMapper: never leaks input contents
        assert mapper.to_mcp_error_any(ValueError("secret-detail-XYZ")) == canonical
        assert mapper.to_mcp_error_any(RuntimeError("api-key-leak")) == canonical
        assert mapper.to_mcp_error_any({"unexpected": "shape"}) == canonical
        assert mapper.to_mcp_error_any(None) == canonical

    def test_to_mcp_error_any_method_form_matches_helper(self, mapper: ErrorMapper) -> None:
        """ErrorMapper.to_mcp_error_any returns the canonical internal-error envelope."""
        from apcore_mcp.adapters.errors import internal_error_response

        assert mapper.to_mcp_error_any(RuntimeError("boom")) == internal_error_response()

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
        # ModuleError doesn't statically declare AI guidance attrs; apcore sets them
        # dynamically at runtime. setattr() signals the dynamic intent to type-checkers.
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


class TestEM3UserFixablePassthrough:
    """[EM-3] apcore 0.24.0 resolves user_fixable at construction time via
    _USER_FIXABLE_BY_CODE; the bridge passes it through _attach_ai_guidance
    with no additional stamping.
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

    def test_apcore_user_fixable_passes_through(self, mapper: ErrorMapper) -> None:
        """user_fixable on the apcore error flows through to userFixable in the result."""
        err = ModuleError(code="VERSION_CONSTRAINT_INVALID", message="x")
        # apcore 0.24.0 auto-sets user_fixable=True for this code
        result = mapper.to_mcp_error(err)
        assert result["userFixable"] is True
        # If apcore explicitly overrides to False, the bridge passes it through
        err.user_fixable = False
        result2 = mapper.to_mcp_error(err)
        assert result2["userFixable"] is False

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


class TestD10003CircuitBreakerOpenAiGuidanceFallback:
    """[D10-003] CIRCUIT_BREAKER_OPEN must surface a canonical aiGuidance hint
    even when the apcore error does not populate ``ai_guidance``. Mirrors the
    TS+Rust fallback string so cross-language envelopes stay byte-identical
    when the per-module hint is missing.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    _CANONICAL_HINT = (
        "Module's circuit breaker is OPEN — repeated failures have tripped the breaker. "
        "Wait until the recovery window elapses, then retry; the breaker will move to "
        "HALF_OPEN and accept a trial call."
    )

    def test_to_mcp_error_circuit_breaker_open_missing_ai_guidance_falls_back_to_canonical_hint(
        self, mapper: ErrorMapper
    ) -> None:
        from apcore.errors import CircuitBreakerOpenError

        err = CircuitBreakerOpenError(module_id="m")
        err.ai_guidance = None  # simulate absent per-module hint

        result = mapper.to_mcp_error(err)

        assert result["errorType"] == "CIRCUIT_BREAKER_OPEN"
        assert result["retryable"] is True
        assert result["aiGuidance"] == self._CANONICAL_HINT

    def test_to_mcp_error_circuit_breaker_open_preserves_per_module_hint(self, mapper: ErrorMapper) -> None:
        """When apcore sets ``ai_guidance``, the per-module value wins."""
        from apcore.errors import CircuitBreakerOpenError

        err = CircuitBreakerOpenError(module_id="m")
        # CircuitBreakerOpenError populates ai_guidance by default
        assert err.ai_guidance  # sanity check
        per_module = err.ai_guidance

        result = mapper.to_mcp_error(err)

        assert result["aiGuidance"] == per_module
        assert result["aiGuidance"] != self._CANONICAL_HINT


class TestD10004DependencyErrorsAttachAiGuidance:
    """[D10-004] DEPENDENCY_NOT_FOUND / DEPENDENCY_VERSION_MISMATCH must flow
    through ``_attach_ai_guidance`` so any apcore-side ``ai_guidance`` /
    ``suggestion`` / ``retryable`` attributes appear on the MCP envelope.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_dependency_not_found_attaches_ai_guidance_fields(self, mapper: ErrorMapper) -> None:
        from apcore.errors import DependencyNotFoundError

        err = DependencyNotFoundError(module_id="m", dependency_id="d")
        err.ai_guidance = "Register dependency 'd' before invoking 'm'."
        err.suggestion = "Add 'd' to the registry."
        err.retryable = False

        result = mapper.to_mcp_error(err)

        assert result["errorType"] == "DEPENDENCY_NOT_FOUND"
        assert result["userFixable"] is True
        assert result["aiGuidance"] == "Register dependency 'd' before invoking 'm'."
        assert result["suggestion"] == "Add 'd' to the registry."
        assert result["retryable"] is False

    def test_dependency_version_mismatch_attaches_ai_guidance_fields(self, mapper: ErrorMapper) -> None:
        from apcore.errors import DependencyVersionMismatchError

        err = DependencyVersionMismatchError(module_id="m", dependency_id="d", required="1.0", actual="0.9")
        err.ai_guidance = "Upgrade 'd' to >=1.0."
        err.suggestion = "Pin 'd' at 1.0 or newer."
        err.retryable = False

        result = mapper.to_mcp_error(err)

        assert result["errorType"] == "DEPENDENCY_VERSION_MISMATCH"
        assert result["userFixable"] is True
        assert result["aiGuidance"] == "Upgrade 'd' to >=1.0."
        assert result["suggestion"] == "Pin 'd' at 1.0 or newer."
        assert result["retryable"] is False


class TestD9003ToMcpErrorAnyDelegatesToModuleError:
    """[D9-003] ``to_mcp_error_any`` must downcast known apcore module-error
    types and delegate to ``to_mcp_error`` (mirrors Rust pattern). Only true
    foreign exceptions fall through to the GENERAL_INTERNAL_ERROR envelope.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_to_mcp_error_any_forwards_module_error_to_typed_path(self, mapper: ErrorMapper) -> None:
        from apcore.errors import ModuleError as ApcoreModuleError

        err = ApcoreModuleError(code="GENERAL_INVALID_INPUT", message="bad shape")
        err.ai_guidance = "Provide 'name' as a string"

        result = mapper.to_mcp_error_any(err)

        assert result["isError"] is True
        assert result["errorType"] == "GENERAL_INVALID_INPUT"
        assert result["message"] == "bad shape"
        assert result["aiGuidance"] == "Provide 'name' as a string"

    def test_to_mcp_error_any_forwards_module_error_subclass(self, mapper: ErrorMapper) -> None:
        from apcore.errors import DependencyNotFoundError

        err = DependencyNotFoundError(module_id="m", dependency_id="d")
        result = mapper.to_mcp_error_any(err)

        assert result["errorType"] == "DEPENDENCY_NOT_FOUND"
        assert result["userFixable"] is True

    def test_to_mcp_error_any_falls_back_for_plain_runtime_error(self, mapper: ErrorMapper) -> None:
        from apcore_mcp.adapters.errors import internal_error_response

        # Non-ModuleError input must still produce the sanitized envelope to
        # avoid leaking server-side state (security: EM-6).
        assert mapper.to_mcp_error_any(RuntimeError("api-key-leak")) == internal_error_response()
        assert mapper.to_mcp_error_any(ValueError("secret")) == internal_error_response()


class TestPyW2TaskLimitExceededFastPath:
    """[Py-W2] A real TaskLimitExceededError is served by the isinstance fast path.

    TaskLimitExceededError is handled by the fast-path isinstance check in
    to_mcp_error() before _handle_apcore_error is ever called. The code-based
    branch inside _handle_apcore_error is NOT dead, however — it serves
    duck-typed and wire-reconstructed errors (see
    TestADEM3CodeBasedDispatchForTypedErrors below).
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_task_limit_exceeded_uses_fast_path_not_handle_apcore_error(self, mapper: ErrorMapper) -> None:
        """A real TaskLimitExceededError must NOT reach _handle_apcore_error."""
        from unittest.mock import patch

        from apcore.errors import TaskLimitExceededError

        err = TaskLimitExceededError(max_tasks=10)

        with patch.object(mapper, "_handle_apcore_error", wraps=mapper._handle_apcore_error) as mock_handle:
            result = mapper.to_mcp_error(err)
            mock_handle.assert_not_called()

        assert result["isError"] is True
        assert result["errorType"] == "TASK_LIMIT_EXCEEDED"
        assert result["retryable"] is True

    def test_task_limit_exceeded_correct_fields(self, mapper: ErrorMapper) -> None:
        """TaskLimitExceededError result contains isError, errorType, retryable."""
        from apcore.errors import TaskLimitExceededError

        err = TaskLimitExceededError(max_tasks=10)
        result = mapper.to_mcp_error(err)

        assert result["isError"] is True
        assert result["errorType"] == "TASK_LIMIT_EXCEEDED"
        assert result["retryable"] is True
        assert "message" in result


class TestADEM3CodeBasedDispatchForTypedErrors:
    """[A-D-EM-3] CIRCUIT_BREAKER_OPEN / TASK_LIMIT_EXCEEDED dispatch on the code too.

    The isinstance fast paths in to_mcp_error only fire for genuine apcore
    exception instances, and CircuitBreakerOpenError does not even exist on
    apcore < 0.20 (the import shim sets it to None). A duck-typed or
    wire-reconstructed ModuleError carrying either code must still get the
    retryable envelope plus aiGuidance, matching errors.ts:253/:268 and
    errors.rs:236/:265.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_duck_typed_task_limit_exceeded_is_retryable(self, mapper: ErrorMapper) -> None:
        """A non-apcore error carrying TASK_LIMIT_EXCEEDED gets the typed envelope."""
        err = ModuleError(
            code="TASK_LIMIT_EXCEEDED",
            message="Task limit exceeded: 10",
            details={"max_tasks": 10},
        )
        result = mapper.to_mcp_error(err)

        assert result["errorType"] == "TASK_LIMIT_EXCEEDED"
        assert result["retryable"] is True

    def test_duck_typed_circuit_breaker_open_gets_guidance(self, mapper: ErrorMapper) -> None:
        """A non-apcore error carrying CIRCUIT_BREAKER_OPEN gets retryable + aiGuidance."""
        err = ModuleError(
            code="CIRCUIT_BREAKER_OPEN",
            message="Circuit breaker is OPEN for module: demo",
            details={"module_id": "demo"},
        )
        result = mapper.to_mcp_error(err)

        assert result["errorType"] == "CIRCUIT_BREAKER_OPEN"
        assert result["retryable"] is True
        assert result["aiGuidance"] == CIRCUIT_BREAKER_RECOVERY_GUIDANCE

    def test_duck_typed_circuit_breaker_open_preserves_per_module_hint(self, mapper: ErrorMapper) -> None:
        """A populated ai_guidance wins over the canonical fallback."""
        err = ModuleError(
            code="CIRCUIT_BREAKER_OPEN",
            message="Circuit breaker is OPEN for module: demo",
            ai_guidance="Retry after 30s.",
        )
        result = mapper.to_mcp_error(err)

        assert result["aiGuidance"] == "Retry after 30s."


class TestADEM4ToMcpErrorNeverRaises:
    """[A-D-EM-4] to_mcp_error must not raise on malformed SchemaValidationError details.

    Spec docs/features/error-mapper.md: "Never raises; all exceptions produce a
    valid error dict". details["errors"] is upstream controlled, so a string or a
    list of non-mappings must degrade to the canonical message rather than blow
    up with AttributeError.
    """

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        return ErrorMapper()

    def test_string_errors_detail_returns_canonical_message(self, mapper: ErrorMapper) -> None:
        """details={"errors": "boom"} must not iterate the string into str.get."""
        err = ModuleError(code="SCHEMA_VALIDATION_ERROR", message="bad", details={"errors": "boom"})
        result = mapper.to_mcp_error(err)

        assert result["isError"] is True
        assert result["errorType"] == "SCHEMA_VALIDATION_ERROR"
        assert result["message"] == "Schema validation failed"

    def test_non_dict_error_entries_are_stringified(self, mapper: ErrorMapper) -> None:
        """details={"errors": [42]} must not call int.get."""
        err = ModuleError(code="SCHEMA_VALIDATION_ERROR", message="bad", details={"errors": [42]})
        result = mapper.to_mcp_error(err)

        assert result["isError"] is True
        assert result["message"] == "Schema validation failed:\n  unknown: 42"

    def test_non_mapping_details_are_narrowed_to_none(self, mapper: ErrorMapper) -> None:
        """A non-mapping details must not break the lookups in _handle_apcore_error."""
        err = ModuleError(code="SCHEMA_VALIDATION_ERROR", message="bad")
        err.details = "not a mapping"  # type: ignore[assignment]
        result = mapper.to_mcp_error(err)

        assert result["isError"] is True
        assert result["details"] is None
        assert result["message"] == "Schema validation failed"

    @pytest.mark.parametrize(
        "code",
        ["APPROVAL_PENDING", "APPROVAL_DENIED", "CONFIG_ENV_MAP_CONFLICT", "PIPELINE_ABORT", "STEP_NOT_FOUND"],
    )
    def test_detail_reading_codes_survive_non_mapping_details(self, mapper: ErrorMapper, code: str) -> None:
        """Every branch that reads details[...] must tolerate a non-mapping."""
        err = ModuleError(code=code, message="boom")
        err.details = ["not", "a", "mapping"]  # type: ignore[assignment]
        result = mapper.to_mcp_error(err)

        assert result["isError"] is True
        assert result["errorType"] == code
