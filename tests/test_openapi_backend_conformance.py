"""Cross-language conformance: the OpenAPI backend.

Drives the Python implementation from the shared fixture at
``apcore-mcp/conformance/fixtures/openapi_backend.json``. The TypeScript and
Rust bridges run the same fixture through their own entry points.

The fixture has three sections, each with its own shape: ``test_cases``
(document -> modules), ``config_cases`` (how the ``spec`` value resolves) and
``error_cases`` (fatal configurations).
"""

from __future__ import annotations

import logging

import pytest

from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer
from apcore_mcp.openapi_backend import openapi_backend, resolve_spec_location
from tests.conformance_fixtures import load_fixture

pytest.importorskip("apcore_toolkit", reason="the OpenAPI backend needs apcore-mcp[openapi]")

_FIXTURE = load_fixture("openapi_backend.json")
_NORMALIZER = ModuleIDNormalizer()


def _build(case: dict, caplog, registry=None):
    options = dict(case.get("options") or {})
    has_other = options.pop("additional_backend_source", False)
    with caplog.at_level(logging.WARNING):
        result = openapi_backend(
            case["document"],
            registry=registry,
            has_other_backend_source=has_other,
            **options,
        )
    return result


# ---------------------------------------------------------------------------
# test_cases — document -> modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _FIXTURE["test_cases"], ids=lambda c: c["id"])
def test_conformance_modules(case: dict, caplog):
    registry = _build(case, caplog)
    descriptors = {mid: registry.get_definition(mid) for mid in registry.list(visibility=["public", "hidden"])}

    expected = case["expected_modules"]
    assert sorted(descriptors) == sorted(
        m["module_id"] for m in expected
    ), f"{case['id']}: module set mismatch — got {sorted(descriptors)}"

    for want in expected:
        mid = want["module_id"]
        descriptor = descriptors[mid]

        # The MCP tool name is the module ID verbatim; OpenAI is dash-normalized.
        if "mcp_tool_name" in want:
            assert mid == want["mcp_tool_name"], f"{case['id']}/{mid}: MCP tool name"
        if "openai_function_name" in want:
            assert _NORMALIZER.normalize(mid) == want["openai_function_name"], f"{case['id']}/{mid}: OpenAI name"

        if "mcp_annotations" in want:
            from apcore_mcp.adapters.annotations import AnnotationMapper

            hints = AnnotationMapper().to_mcp_annotations(descriptor.annotations)
            for key, value in want["mcp_annotations"].items():
                assert hints.get(key) == value, f"{case['id']}/{mid}: {key}"

        if "requires_approval" in want:
            assert bool(descriptor.annotations.requires_approval) == want["requires_approval"], (
                f"{case['id']}/{mid}: requires_approval — the scanner never infers it, and this "
                f"case pins the gap as a fact"
            )

        if "warnings_contain" in want:
            assert any(
                want["warnings_contain"] in str(r.message) for r in caplog.records
            ), f"{case['id']}/{mid}: expected a log record containing {want['warnings_contain']!r}"

    for skip in case.get("expected_skipped") or []:
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert skip["derived_module_id"] in joined, (
            f"{case['id']}: no warning named the skipped operation {skip['derived_module_id']!r}. "
            f"A transform_module returning None drops the module SILENTLY — the warning is the "
            f"bridge's to emit."
        )
        assert skip["reason_substring"] in joined, f"{case['id']}: skip warning did not name the offending segment"


# ---------------------------------------------------------------------------
# config_cases — how the `spec` value resolves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _FIXTURE["config_cases"], ids=lambda c: c["id"])
def test_conformance_spec_resolution(case: dict, caplog):
    with caplog.at_level(logging.WARNING):
        resolved = resolve_spec_location(case["spec_value"], project_root=case["project_root"])

    if resolved is None:
        # Discarded: the caller falls through to the next configuration tier.
        assert "spec_value_next_tier" in case, f"{case['id']}: value discarded but no next tier declared"
        resolved = resolve_spec_location(case["spec_value_next_tier"], project_root=case["project_root"])

    assert resolved == case["expected_resolved_spec"], f"{case['id']}: resolved to {resolved!r}"

    if case.get("expected_warning_substring"):
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert case["expected_warning_substring"] in joined, f"{case['id']}: missing discard warning"


# ---------------------------------------------------------------------------
# error_cases — fatal configurations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _FIXTURE["error_cases"], ids=lambda c: c["id"])
def test_conformance_error_case(case: dict, caplog):
    from apcore import Registry

    registry = None
    preexisting = case.get("preexisting_registry_module_ids")
    if preexisting:
        registry = Registry()
        for mid in preexisting:
            _register_stub(registry, mid)

    with pytest.raises((ValueError, RuntimeError)) as exc_info:
        _build(case, caplog, registry=registry)

    message = str(exc_info.value)
    for fragment in case["expected_error_substrings"]:
        assert fragment in message, f"{case['id']}: error {message!r} missing {fragment!r}"

    if "expected_registry_module_ids_after" in case:
        after = sorted(registry.list(visibility=["public", "hidden"]))
        assert after == sorted(
            case["expected_registry_module_ids_after"]
        ), f"{case['id']}: the preflight must register NOTHING — registry is now {after}"


def _register_stub(registry, module_id: str) -> None:
    """Register a minimal module so the preflight has something to collide with."""
    from apcore import FunctionModule

    registry.register(
        module_id,
        FunctionModule(
            module_id=module_id,
            description="stub",
            func=lambda **_: {},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
    )
