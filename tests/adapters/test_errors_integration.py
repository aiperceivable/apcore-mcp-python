"""Integration tests for ErrorMapper with real apcore errors."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apcore_mcp.adapters.errors import ErrorMapper

# Add apcore to path if available
apcore_path = Path("/Users/tercel/WorkSpace/aiperceivable/apcore-python/src")
if apcore_path.exists():
    sys.path.insert(0, str(apcore_path))

try:
    from apcore.errors import (
        ACLDeniedError,
        CallDepthExceededError,
        CircularCallError,
        InvalidInputError,
        ModuleNotFoundError,
        ModuleTimeoutError,
        SchemaValidationError,
    )

    APCORE_AVAILABLE = True
except ImportError:
    APCORE_AVAILABLE = False


@pytest.mark.skipif(not APCORE_AVAILABLE, reason="apcore-python not available")
class TestErrorMapperIntegration:
    """Integration tests with real apcore errors."""

    @pytest.fixture
    def mapper(self) -> ErrorMapper:
        """Create an ErrorMapper instance."""
        return ErrorMapper()

    def test_real_module_not_found(self, mapper: ErrorMapper) -> None:
        """Test with real ModuleNotFoundError."""
        error = ModuleNotFoundError("image.resize")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "MODULE_NOT_FOUND"
        assert "image.resize" in result["message"]

    def test_real_schema_validation_error(self, mapper: ErrorMapper) -> None:
        """Test with real SchemaValidationError."""
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

    def test_real_acl_denied(self, mapper: ErrorMapper) -> None:
        """Test with real ACLDeniedError - should sanitize."""
        error = ACLDeniedError("user1", "admin.delete")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "ACL_DENIED"
        assert "access denied" in result["message"].lower()
        # Should NOT contain sensitive caller_id
        assert "user1" not in result["message"]

    def test_real_module_timeout(self, mapper: ErrorMapper) -> None:
        """Test with real ModuleTimeoutError."""
        error = ModuleTimeoutError("slow.module", 5000)
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "MODULE_TIMEOUT"
        assert "timeout" in result["message"].lower() or "timed out" in result["message"].lower()

    def test_real_invalid_input(self, mapper: ErrorMapper) -> None:
        """Test with real InvalidInputError."""
        error = InvalidInputError("missing field X")
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "GENERAL_INVALID_INPUT"
        assert "missing field X" in result["message"]

    def test_real_call_depth_exceeded(self, mapper: ErrorMapper) -> None:
        """Test with real CallDepthExceededError - should be internal error."""
        error = CallDepthExceededError(10, 10, ["a", "b"])
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "CALL_DEPTH_EXCEEDED"
        assert "internal" in result["message"].lower()

    def test_real_circular_call(self, mapper: ErrorMapper) -> None:
        """Test with real CircularCallError - should be internal error."""
        error = CircularCallError("a", ["a", "b", "a"])
        result = mapper.to_mcp_error(error)

        assert result["isError"] is True
        assert result["errorType"] == "CIRCULAR_CALL"
        assert "internal" in result["message"].lower()
