"""Tests for ACL exposure — builder + serve()/APCoreMCP integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apcore_mcp._utils import resolve_executor
from apcore_mcp.acl_builder import build_acl_from_config

# ---------------------------------------------------------------------------
# build_acl_from_config — unit tests
# ---------------------------------------------------------------------------


def test_build_empty_returns_none():
    assert build_acl_from_config(None) is None
    assert build_acl_from_config({}) is None


def test_build_rules_with_default_effect_deny():
    from apcore import ACL

    acl = build_acl_from_config(
        {
            "default_effect": "deny",
            "rules": [
                {"callers": ["role:admin"], "targets": ["sys.*"], "effect": "allow"},
            ],
        }
    )
    assert isinstance(acl, ACL)


def test_build_with_default_effect_allow():
    from apcore import ACL

    acl = build_acl_from_config({"default_effect": "allow", "rules": []})
    assert isinstance(acl, ACL)


def test_default_effect_defaults_to_deny_when_omitted():
    acl = build_acl_from_config({"rules": [{"callers": ["*"], "targets": ["public.*"], "effect": "allow"}]})
    assert acl is not None


def test_rule_with_description_and_conditions():
    from apcore import ACL

    acl = build_acl_from_config(
        {
            "rules": [
                {
                    "callers": ["role:admin"],
                    "targets": ["sys.*"],
                    "effect": "allow",
                    "description": "admin access",
                    "conditions": {"identity_types": ["human"]},
                }
            ]
        }
    )
    assert isinstance(acl, ACL)


def test_invalid_default_effect_raises():
    with pytest.raises(ValueError, match="default_effect must be"):
        build_acl_from_config({"default_effect": "maybe", "rules": []})


def test_rule_missing_callers_raises():
    with pytest.raises(ValueError, match="'callers' must be a non-empty list"):
        build_acl_from_config({"rules": [{"targets": ["x.*"], "effect": "allow"}]})


def test_rule_missing_targets_raises():
    with pytest.raises(ValueError, match="'targets' must be a non-empty list"):
        build_acl_from_config({"rules": [{"callers": ["*"], "effect": "allow"}]})


def test_rule_invalid_effect_raises():
    with pytest.raises(ValueError, match="'effect' must be 'allow' or 'deny'"):
        build_acl_from_config({"rules": [{"callers": ["*"], "targets": ["*"], "effect": "maybe"}]})


def test_rule_unknown_key_raises():
    with pytest.raises(ValueError, match="unexpected keys"):
        build_acl_from_config(
            {
                "rules": [
                    {
                        "callers": ["*"],
                        "targets": ["*"],
                        "effect": "allow",
                        "bogus": True,
                    }
                ]
            }
        )


def test_non_mapping_config_raises():
    with pytest.raises(ValueError, match="must be a mapping"):
        build_acl_from_config("deny")  # type: ignore[arg-type]


def test_rules_non_list_raises():
    with pytest.raises(ValueError, match="rules must be a list"):
        build_acl_from_config({"rules": "oops"})


# ---------------------------------------------------------------------------
# resolve_executor — acl wiring
# ---------------------------------------------------------------------------


class _Exec:
    """Executor stub: call_async + use() + set_acl()."""

    def __init__(self):
        self.used: list[object] = []
        self.installed_acl: object | None = None

    async def call_async(self, *_args, **_kwargs):
        return {}

    def use(self, mw):
        self.used.append(mw)
        return self

    def set_acl(self, acl):
        self.installed_acl = acl


def test_resolve_executor_installs_acl_on_existing_executor():
    exc = _Exec()
    acl = MagicMock()
    result = resolve_executor(exc, acl=acl)
    assert result is exc
    assert exc.installed_acl is acl


def test_resolve_executor_acl_none_leaves_executor_alone():
    exc = _Exec()
    resolve_executor(exc)
    assert exc.installed_acl is None


def test_resolve_executor_errors_when_executor_has_no_set_acl():
    class _NoSetAcl:
        async def call_async(self, *_args, **_kwargs):
            return {}

    exc = _NoSetAcl()
    with pytest.raises(RuntimeError, match="does not support .set_acl"):
        resolve_executor(exc, acl=MagicMock())


# ---------------------------------------------------------------------------
# APCoreMCP — Config Bus integration + precedence
# ---------------------------------------------------------------------------


def test_apcore_mcp_reads_acl_from_config_bus():
    from apcore import ACL, Registry

    from apcore_mcp.apcore_mcp import APCoreMCP

    registry = Registry()
    fake_config = MagicMock()
    fake_config.get.side_effect = lambda key: {
        "mcp.middleware": None,
        "mcp.acl": {
            "default_effect": "deny",
            "rules": [
                {"callers": ["*"], "targets": ["*"], "effect": "allow"},
            ],
        },
    }.get(key)

    with patch("apcore.Config.load", return_value=fake_config):
        mcp = APCoreMCP(registry)

    # The executor should have an ACL installed (not None) — the apcore 0.18+
    # Executor exposes it via acl() snapshot or the internal _acl attribute.
    acl_attr = getattr(mcp._executor, "acl", None)
    # Python stores it as a private attribute; read via either path.
    acl_value = acl_attr() if callable(acl_attr) else acl_attr
    if acl_value is None:
        acl_value = getattr(mcp._executor, "_acl", None)
    assert acl_value is not None and isinstance(acl_value, ACL)


def test_apcore_mcp_caller_acl_overrides_config_bus():
    from apcore import ACL, ACLRule, Registry

    from apcore_mcp.apcore_mcp import APCoreMCP

    registry = Registry()

    fake_config = MagicMock()
    fake_config.get.side_effect = lambda key: {
        "mcp.middleware": None,
        "mcp.acl": {
            "default_effect": "deny",
            "rules": [
                {"callers": ["*"], "targets": ["*"], "effect": "allow"},
            ],
        },
    }.get(key)

    explicit_acl = ACL(
        rules=[ACLRule(callers=["admin"], targets=["*"], effect="allow")],
        default_effect="deny",
    )

    with patch("apcore.Config.load", return_value=fake_config):
        mcp = APCoreMCP(registry, acl=explicit_acl)

    # Verify the executor holds the *explicit* ACL, not the Config Bus one.
    acl_getter = getattr(mcp._executor, "acl", None)
    acl_value = acl_getter() if callable(acl_getter) else acl_getter
    if acl_value is None:
        acl_value = getattr(mcp._executor, "_acl", None)
    assert acl_value is explicit_acl
