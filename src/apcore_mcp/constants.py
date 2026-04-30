"""APCore MCP bridge constants."""

from __future__ import annotations

import re

REGISTRY_EVENTS: dict[str, str] = {
    "REGISTER": "register",
    "UNREGISTER": "unregister",
}

ERROR_CODES: dict[str, str] = {
    "MODULE_NOT_FOUND": "MODULE_NOT_FOUND",
    "MODULE_DISABLED": "MODULE_DISABLED",
    "SCHEMA_VALIDATION_ERROR": "SCHEMA_VALIDATION_ERROR",
    "ACL_DENIED": "ACL_DENIED",
    "CALL_DEPTH_EXCEEDED": "CALL_DEPTH_EXCEEDED",
    "CIRCULAR_CALL": "CIRCULAR_CALL",
    "CALL_FREQUENCY_EXCEEDED": "CALL_FREQUENCY_EXCEEDED",
    "INTERNAL_ERROR": "INTERNAL_ERROR",
    "MODULE_TIMEOUT": "MODULE_TIMEOUT",
    "MODULE_LOAD_ERROR": "MODULE_LOAD_ERROR",
    "MODULE_EXECUTE_ERROR": "MODULE_EXECUTE_ERROR",
    "GENERAL_INVALID_INPUT": "GENERAL_INVALID_INPUT",
    "APPROVAL_DENIED": "APPROVAL_DENIED",
    "APPROVAL_TIMEOUT": "APPROVAL_TIMEOUT",
    "APPROVAL_PENDING": "APPROVAL_PENDING",
    "VERSION_INCOMPATIBLE": "VERSION_INCOMPATIBLE",
    "ERROR_CODE_COLLISION": "ERROR_CODE_COLLISION",
    "EXECUTION_CANCELLED": "EXECUTION_CANCELLED",
    "CONFIG_NAMESPACE_DUPLICATE": "CONFIG_NAMESPACE_DUPLICATE",
    "CONFIG_NAMESPACE_RESERVED": "CONFIG_NAMESPACE_RESERVED",
    "CONFIG_ENV_PREFIX_CONFLICT": "CONFIG_ENV_PREFIX_CONFLICT",
    "CONFIG_MOUNT_ERROR": "CONFIG_MOUNT_ERROR",
    "CONFIG_BIND_ERROR": "CONFIG_BIND_ERROR",
    "ERROR_FORMATTER_DUPLICATE": "ERROR_FORMATTER_DUPLICATE",
    "CONFIG_ENV_MAP_CONFLICT": "CONFIG_ENV_MAP_CONFLICT",
    "PIPELINE_ABORT": "PIPELINE_ABORT",
    "STEP_NOT_FOUND": "STEP_NOT_FOUND",
    # apcore 0.19.0: dependency resolution & task manager
    "DEPENDENCY_NOT_FOUND": "DEPENDENCY_NOT_FOUND",
    "DEPENDENCY_VERSION_MISMATCH": "DEPENDENCY_VERSION_MISMATCH",
    "TASK_LIMIT_EXCEEDED": "TASK_LIMIT_EXCEEDED",
    "VERSION_CONSTRAINT_INVALID": "VERSION_CONSTRAINT_INVALID",
    # apcore 0.19.0: binding/schema mode errors (DECLARATIVE_CONFIG_SPEC)
    "BINDING_SCHEMA_INFERENCE_FAILED": "BINDING_SCHEMA_INFERENCE_FAILED",
    "BINDING_SCHEMA_MODE_CONFLICT": "BINDING_SCHEMA_MODE_CONFLICT",
    "BINDING_STRICT_SCHEMA_INCOMPATIBLE": "BINDING_STRICT_SCHEMA_INCOMPATIBLE",
    "BINDING_POLICY_VIOLATION": "BINDING_POLICY_VIOLATION",
}

MODULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

#: Dot-namespaced event types introduced in apcore 0.15.0 (§9.16).
#:
#: These are canonical event names for the apcore event system. Consumers
#: subscribing via ``registry.on(name)`` should reference these constants
#: rather than the literal strings; the legacy ``"module_health_changed"``
#: and ``"config_changed"`` names are partitioned across these four events.
#:
#: Cross-language equivalents:
#:   - TypeScript: ``APCORE_EVENTS`` in ``src/types.ts``
#:   - Rust:       ``apcore_mcp::apcore_events`` module in ``src/constants.rs``
APCORE_EVENTS: dict[str, str] = {
    "MODULE_TOGGLED": "apcore.module.toggled",
    "MODULE_RELOADED": "apcore.module.reloaded",
    "CONFIG_UPDATED": "apcore.config.updated",
    "HEALTH_RECOVERED": "apcore.health.recovered",
}
