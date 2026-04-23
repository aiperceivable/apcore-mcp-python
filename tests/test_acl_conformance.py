"""Cross-language conformance: ACL Config Bus loading.

Drives the Python builder from the shared fixture at
``apcore-mcp/conformance/fixtures/acl_config.json``. The TypeScript and Rust
bridges run the same fixture through their own builders; all three
implementations must agree on (rule_count, default_effect) and on which
inputs are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apcore_mcp.acl_builder import build_acl_from_config

_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "apcore-mcp" / "conformance" / "fixtures" / "acl_config.json"


def _load_fixture() -> dict:
    if not _FIXTURE_PATH.is_file():
        pytest.skip(f"conformance fixture not found at {_FIXTURE_PATH}")
    with _FIXTURE_PATH.open() as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _load_fixture()["test_cases"],
    ids=lambda c: c["id"],
)
def test_conformance_success_case(case: dict):
    result = build_acl_from_config(case["input"])
    expected = case["expected_acl"]
    if expected is None:
        assert result is None, f"{case['id']}: expected no ACL, got {result!r}"
        return

    assert result is not None, f"{case['id']}: expected ACL, got None"
    # Access the rule count via the documented `rules()` accessor or private fallback.
    rules = getattr(result, "rules", None)
    rule_list = rules() if callable(rules) else getattr(result, "_rules", [])
    assert len(rule_list) == expected["rule_count"], (
        f"{case['id']}: rule_count mismatch — got {len(rule_list)}, " f"expected {expected['rule_count']}"
    )
    # default_effect accessor is a private attribute in Python; check both.
    default_effect = getattr(result, "default_effect", None) or getattr(result, "_default_effect", None)
    assert default_effect == expected["default_effect"], (
        f"{case['id']}: default_effect mismatch — got {default_effect!r}, " f"expected {expected['default_effect']!r}"
    )


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _load_fixture()["error_cases"],
    ids=lambda c: c["id"],
)
def test_conformance_error_case(case: dict):
    with pytest.raises(ValueError) as exc_info:
        build_acl_from_config(case["input"])
    assert case["expected_error_substring"] in str(exc_info.value), (
        f"{case['id']}: error message {exc_info.value!r} missing substring " f"{case['expected_error_substring']!r}"
    )
