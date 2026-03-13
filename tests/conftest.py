"""Shared test fixtures for apcore-mcp tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Lightweight stubs for apcore types used in unit tests.
# These mirror the real apcore API surface without importing apcore,
# so that adapter/converter unit tests have zero external dependencies.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleAnnotations:
    """Stub for apcore.module.ModuleAnnotations."""

    readonly: bool = False
    destructive: bool = False
    idempotent: bool = False
    requires_approval: bool = False
    open_world: bool = True
    streaming: bool = False
    cacheable: bool = False
    cache_ttl: int = 0
    cache_key_fields: list[str] | None = None
    paginated: bool = False
    pagination_style: str = "cursor"


@dataclass
class ModuleExample:
    """Stub for apcore.module.ModuleExample."""

    title: str
    inputs: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


@dataclass
class ModuleDescriptor:
    """Stub for apcore.registry.types.ModuleDescriptor."""

    module_id: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    name: str | None = None
    documentation: str | None = None
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    annotations: ModuleAnnotations | None = None
    examples: list[ModuleExample] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fixtures: reusable ModuleDescriptor instances for tests
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_descriptor() -> ModuleDescriptor:
    """A simple module with flat input schema."""
    return ModuleDescriptor(
        module_id="image.resize",
        name="Image Resize",
        description="Resize an image to the specified dimensions",
        input_schema={
            "type": "object",
            "properties": {
                "width": {"type": "integer", "description": "Target width in pixels"},
                "height": {"type": "integer", "description": "Target height in pixels"},
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file",
                },
            },
            "required": ["width", "height", "image_path"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "original_size": {"type": "array", "items": {"type": "integer"}},
                "new_size": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["output_path"],
        },
        tags=["image", "transform"],
        annotations=ModuleAnnotations(idempotent=True),
    )


@pytest.fixture
def empty_schema_descriptor() -> ModuleDescriptor:
    """A module with empty input/output schemas."""
    return ModuleDescriptor(
        module_id="system.ping",
        description="Health check ping",
        input_schema={},
        output_schema={},
        annotations=ModuleAnnotations(readonly=True, idempotent=True),
    )


@pytest.fixture
def nested_schema_descriptor() -> ModuleDescriptor:
    """A module with nested/complex input schema including $defs."""
    return ModuleDescriptor(
        module_id="workflow.execute",
        name="Workflow Execute",
        description="Execute a multi-step workflow",
        input_schema={
            "type": "object",
            "$defs": {
                "Step": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["name"],
                }
            },
            "properties": {
                "workflow_name": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Step"},
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["workflow_name", "steps"],
        },
        output_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": {"type": "object"}},
                "success": {"type": "boolean"},
            },
        },
        annotations=ModuleAnnotations(destructive=True, requires_approval=True, open_world=False),
    )


@pytest.fixture
def destructive_descriptor() -> ModuleDescriptor:
    """A module marked as destructive."""
    return ModuleDescriptor(
        module_id="file.delete",
        description="Delete a file from disk",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
        output_schema={
            "type": "object",
            "properties": {"deleted": {"type": "boolean"}},
        },
        annotations=ModuleAnnotations(destructive=True, requires_approval=True, open_world=False),
    )


@pytest.fixture
def no_annotations_descriptor() -> ModuleDescriptor:
    """A module with no annotations (None)."""
    return ModuleDescriptor(
        module_id="text.echo",
        description="Echo the input text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        output_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
        annotations=None,
    )


@pytest.fixture
def all_types_descriptor() -> ModuleDescriptor:
    """A module using all JSON Schema types in its input."""
    return ModuleDescriptor(
        module_id="test.all_types",
        description="Test module with all JSON Schema types",
        input_schema={
            "type": "object",
            "properties": {
                "str_field": {"type": "string"},
                "int_field": {"type": "integer"},
                "num_field": {"type": "number"},
                "bool_field": {"type": "boolean"},
                "null_field": {"type": "null"},
                "arr_field": {"type": "array", "items": {"type": "string"}},
                "obj_field": {
                    "type": "object",
                    "properties": {"nested": {"type": "string"}},
                },
            },
        },
        output_schema={"type": "object"},
    )
