"""Tests for MCP config namespace registration."""

from __future__ import annotations

from apcore.config import Config

from apcore_mcp.config import MCP_DEFAULTS, MCP_ENV_PREFIX, MCP_NAMESPACE, register_mcp_namespace


class TestMcpConfigNamespace:
    """Test that the mcp namespace is registered with the Config Bus."""

    def test_mcp_namespace_registered(self):
        """The 'mcp' namespace should be registered after importing apcore_mcp."""
        register_mcp_namespace()
        namespaces = Config.registered_namespaces()
        ns_names = [ns["name"] if isinstance(ns, dict) else ns for ns in namespaces]
        assert "mcp" in ns_names

    def test_mcp_namespace_constant(self):
        assert MCP_NAMESPACE == "mcp"

    def test_mcp_env_prefix(self):
        """The mcp namespace should use APCORE_MCP env prefix."""
        assert MCP_ENV_PREFIX == "APCORE_MCP"

    def test_mcp_defaults_transport(self):
        assert MCP_DEFAULTS["transport"] == "stdio"

    def test_mcp_defaults_host(self):
        assert MCP_DEFAULTS["host"] == "127.0.0.1"

    def test_mcp_defaults_port(self):
        assert MCP_DEFAULTS["port"] == 8000

    def test_register_idempotent(self):
        """Calling register_mcp_namespace() twice should not raise."""
        register_mcp_namespace()
        register_mcp_namespace()
