"""Cross-language conformance: SchemaConverter $ref sibling-key preservation.

Drives ``SchemaConverter.convert_input_schema`` from the shared fixture at
``apcore-mcp/conformance/fixtures/schema_converter.json``. The TypeScript and
Rust bridges run the same fixture through their own converters; all three
must shallow-merge a $ref node's sibling keys over the resolved definition,
with the sibling winning on conflict, instead of discarding them.

This is a security fixture, not a fidelity one: the router's output
redaction (see ``output_redaction.json`` /
``test_output_redaction_conformance.py``) reads ``x-sensitive`` off the
*resolved* schema to decide what to mask, so losing a sibling key here is a
credential-disclosure path, not a cosmetic bug.

Called with ``strict=False`` per the fixture's ``entry_point`` so the
strict-mode ``additionalProperties: false`` injection — a separate, already
pinned concern (see ``tests/adapters/test_schema.py``) — does not add noise
to the expected output.
"""

from __future__ import annotations

import pytest
from apcore import ModuleDescriptor

from apcore_mcp.adapters.schema import SchemaConverter
from tests.conformance_fixtures import load_fixture

_FIXTURE = load_fixture("schema_converter.json")


def _descriptor(input_schema: dict) -> ModuleDescriptor:
    return ModuleDescriptor(
        module_id="conformance.schema_converter",
        name=None,
        description="",
        documentation=None,
        input_schema=input_schema,
        output_schema={},
    )


@pytest.mark.parametrize("case", _FIXTURE["test_cases"], ids=lambda c: c["id"])
def test_conformance_schema_converter_inlining(case: dict):
    converter = SchemaConverter()
    result = converter.convert_input_schema(_descriptor(case["input_schema"]), strict=False)

    assert result == case["expected_inlined_schema"], f"{case['id']}: inlined schema mismatch"


@pytest.mark.parametrize("case", _FIXTURE["error_cases"], ids=lambda c: c["id"])
def test_conformance_schema_converter_errors(case: dict):
    converter = SchemaConverter()

    with pytest.raises(Exception) as exc_info:
        converter.convert_input_schema(_descriptor(case["input_schema"]), strict=False)

    assert case["expected_error_substring"] in str(exc_info.value), (
        f"{case['id']}: expected {case['expected_error_substring']!r} in error message, "
        f"got {exc_info.value!r}"
    )
