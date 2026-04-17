"""MCP config namespace registration for the Config Bus (apcore 0.15.0 §9.4)."""

from __future__ import annotations

import logging

from apcore.config import Config

logger = logging.getLogger(__name__)

MCP_NAMESPACE = "mcp"
MCP_ENV_PREFIX = "APCORE_MCP"

MCP_DEFAULTS: dict[str, object] = {
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8000,
    "name": "apcore-mcp",
    "log_level": None,
    "validate_inputs": False,
    "explorer": False,
    "explorer_prefix": "/explorer",
    "require_auth": True,
    # Declarative middleware list — each entry is {type: str, ...kwargs}.
    # See middleware_builder.build_middleware_from_config for supported types.
    "middleware": [],
    # Declarative ACL — {default_effect: "deny"|"allow", rules: [ACLRule...]}.
    # Empty / null means "no ACL" (allow all). See acl_builder.build_acl_from_config.
    "acl": None,
}


def register_mcp_namespace() -> None:
    """Register the 'mcp' config namespace. Safe to call multiple times."""
    try:
        Config.register_namespace(
            MCP_NAMESPACE,
            env_prefix=MCP_ENV_PREFIX,
            defaults=MCP_DEFAULTS,
        )
    except Exception:
        logger.debug("MCP config namespace already registered")
