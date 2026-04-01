"""MCP error formatter registered with apcore's ErrorFormatterRegistry (§8.8)."""

from __future__ import annotations

import logging
from typing import Any

from apcore.error_formatter import ErrorFormatterRegistry
from apcore.errors import ModuleError

from apcore_mcp.adapters.errors import ErrorMapper

logger = logging.getLogger(__name__)


class MCPErrorFormatter:
    """Adapts ErrorMapper to the apcore ErrorFormatter protocol."""

    def __init__(self) -> None:
        self._mapper = ErrorMapper()

    def format(self, error: ModuleError, context: object = None) -> dict[str, Any]:
        """Format an apcore error into an MCP error response."""
        return self._mapper.to_mcp_error(error)


def register_mcp_formatter() -> None:
    """Register the MCP error formatter. Safe to call multiple times."""
    try:
        ErrorFormatterRegistry.register("mcp", MCPErrorFormatter())
    except Exception:
        logger.debug("MCP error formatter already registered")
