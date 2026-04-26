"""Cross-language conformance: middleware Config Bus loading.

Drives the Python builder from the shared fixture at
``apcore-mcp/conformance/fixtures/middleware_config.json``. The TypeScript
and Rust bridges run the same fixture through their own builders; all three
implementations must agree on the resulting middleware names and on which
inputs are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apcore_mcp.middleware_builder import build_middleware_from_config

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "apcore-mcp" / "conformance" / "fixtures" / "middleware_config.json"
)


def _load_fixture() -> dict:
    if not _FIXTURE_PATH.is_file():
        pytest.skip(
            f"conformance fixture not found at {_FIXTURE_PATH} — "
            "is the apcore-mcp monorepo checked out alongside apcore-mcp-python?",
            allow_module_level=True,
        )
    with _FIXTURE_PATH.open() as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Built-in name mapping (apcore Middleware.name() ↔ conformance label)
# ---------------------------------------------------------------------------


_CLASS_TO_LABEL = {
    "RetryMiddleware": "retry",
    "LoggingMiddleware": "logging",
    "ErrorHistoryMiddleware": "error_history",
}


def _labels(instances: list[object]) -> list[str]:
    out: list[str] = []
    for mw in instances:
        cls = mw.__class__.__name__
        label = _CLASS_TO_LABEL.get(cls)
        if label is None:
            raise AssertionError(f"Unexpected middleware class {cls!r}")
        out.append(label)
    return out


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _load_fixture()["test_cases"],
    ids=lambda c: c["id"],
)
def test_conformance_success_case(case: dict):
    result = build_middleware_from_config(case["input_entries"])
    assert _labels(result) == case["expected_middleware_names"], (
        f"{case['id']}: got {_labels(result)}, expected " f"{case['expected_middleware_names']}"
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
        build_middleware_from_config(case["input_entries"])
    assert case["expected_error_substring"] in str(exc_info.value), (
        f"{case['id']}: error message {exc_info.value!r} missing substring " f"{case['expected_error_substring']!r}"
    )
