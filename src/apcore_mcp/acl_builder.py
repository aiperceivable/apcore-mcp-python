"""Build an apcore ACL instance from a Config Bus `mcp.acl` section.

Config Bus schema (YAML):

.. code-block:: yaml

    mcp:
      acl:
        default_effect: deny         # or "allow" — default is "deny" (fail-secure)
        rules:
          - callers: ["role:admin"]
            targets: ["sys.*"]
            effect: allow
            description: "Admins can reach system modules"
          - callers: ["*"]
            targets: ["sys.reload", "sys.toggle"]
            effect: deny
            description: "Runtime control is admin-only"
            conditions:
              identity_types: ["human", "system"]

The bridge accepts this dict and constructs an ``apcore.ACL`` with the given
rules and default effect. Invalid entries fail loudly at startup. The same
schema is consumed by the TypeScript and Rust bridges via the shared
``conformance/fixtures/acl_config.json`` fixture.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ALLOWED_EFFECTS = frozenset({"allow", "deny"})
_ALLOWED_RULE_KEYS = frozenset({"callers", "targets", "effect", "description", "conditions"})


def build_acl_from_config(acl_config: Any | None) -> Any | None:
    """Construct an ``apcore.ACL`` from a Config Bus ``mcp.acl`` mapping.

    Returns ``None`` when ``acl_config`` is falsy (no ACL section configured).
    Raises :class:`ValueError` on malformed entries so misconfiguration fails
    loudly at startup.
    """
    if not acl_config:
        return None

    if not isinstance(acl_config, dict):
        raise ValueError(
            f"mcp.acl must be a mapping with 'rules' and optional " f"'default_effect', got {type(acl_config).__name__}"
        )

    try:
        from apcore import ACL, ACLRule
    except ImportError as exc:
        raise RuntimeError("Config Bus `mcp.acl` requires apcore>=0.18 with ACL support") from exc

    default_effect = acl_config.get("default_effect", "deny")
    if default_effect not in _ALLOWED_EFFECTS:
        raise ValueError(f"mcp.acl.default_effect must be 'allow' or 'deny', got {default_effect!r}")

    raw_rules = acl_config.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"mcp.acl.rules must be a list, got {type(raw_rules).__name__}")

    rules: list[Any] = []
    for idx, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ValueError(f"mcp.acl.rules[{idx}] must be an object, got {type(entry).__name__}")
        extra = set(entry.keys()) - _ALLOWED_RULE_KEYS
        if extra:
            raise ValueError(f"mcp.acl.rules[{idx}] got unexpected keys: {sorted(extra)}")

        callers = entry.get("callers")
        targets = entry.get("targets")
        effect = entry.get("effect")

        if not isinstance(callers, list) or not callers:
            raise ValueError(f"mcp.acl.rules[{idx}] 'callers' must be a non-empty list")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"mcp.acl.rules[{idx}] 'targets' must be a non-empty list")
        if effect not in _ALLOWED_EFFECTS:
            raise ValueError(f"mcp.acl.rules[{idx}] 'effect' must be 'allow' or 'deny', got {effect!r}")

        rule_kwargs: dict[str, Any] = {
            "callers": list(callers),
            "targets": list(targets),
            "effect": effect,
        }
        if "description" in entry:
            rule_kwargs["description"] = entry["description"] or ""
        if "conditions" in entry and entry["conditions"] is not None:
            if not isinstance(entry["conditions"], dict):
                raise ValueError(f"mcp.acl.rules[{idx}] 'conditions' must be an object or null")
            rule_kwargs["conditions"] = entry["conditions"]

        rules.append(ACLRule(**rule_kwargs))

    logger.info("Built ACL with %d rule(s), default_effect=%s", len(rules), default_effect)
    return ACL(rules=rules, default_effect=default_effect)
