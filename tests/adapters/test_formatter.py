"""Tests for MCPErrorFormatter and registry integration."""

from __future__ import annotations

from apcore.error_formatter import ErrorFormatterRegistry
from apcore.errors import ModuleError

from apcore_mcp.adapters.formatter import MCPErrorFormatter, register_mcp_formatter


class TestMCPErrorFormatter:
    """Test the MCP error formatter."""

    def test_format_returns_dict(self):
        """format() should return a dict (CallToolResult-like structure)."""
        formatter = MCPErrorFormatter()
        error = ModuleError(code="TEST_ERROR", message="test")
        result = formatter.format(error)
        assert isinstance(result, dict)

    def test_format_module_not_found(self):
        """format() should handle MODULE_NOT_FOUND errors."""
        formatter = MCPErrorFormatter()
        error = ModuleError(code="MODULE_NOT_FOUND", message="Module not found: test.mod")
        result = formatter.format(error)
        assert isinstance(result, dict)


class TestMCPFormatterRegistration:
    """Test error formatter registry integration."""

    def test_register_mcp_formatter_idempotent(self):
        """Calling register_mcp_formatter() twice should not raise."""
        register_mcp_formatter()
        register_mcp_formatter()

    def test_formatter_registered_in_registry(self):
        """The MCP formatter should be discoverable via ErrorFormatterRegistry."""
        register_mcp_formatter()
        formatter = ErrorFormatterRegistry.get("mcp")
        assert formatter is not None
        assert isinstance(formatter, MCPErrorFormatter)
