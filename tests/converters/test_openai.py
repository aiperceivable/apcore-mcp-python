"""Tests for OpenAIConverter."""

from __future__ import annotations

import pytest

from apcore_mcp.converters.openai import OpenAIConverter
from tests.conftest import ModuleAnnotations, ModuleDescriptor

# ---------------------------------------------------------------------------
# Lightweight stub for apcore Registry used in converter tests.
# ---------------------------------------------------------------------------


class StubRegistry:
    def __init__(self, descriptors: list[ModuleDescriptor]):
        self._descriptors = {d.module_id: d for d in descriptors}

    def list(self, tags=None, prefix=None):
        ids = list(self._descriptors.keys())
        if prefix is not None:
            ids = [mid for mid in ids if mid.startswith(prefix)]
        if tags is not None:
            tag_set = set(tags)
            ids = [mid for mid in ids if tag_set.issubset(set(self._descriptors[mid].tags))]
        return sorted(ids)

    def get_definition(self, module_id):
        return self._descriptors.get(module_id)


class TestConvertDescriptor:
    """Tests for OpenAIConverter.convert_descriptor."""

    @pytest.fixture
    def converter(self):
        return OpenAIConverter()

    def test_convert_simple_descriptor(self, converter, simple_descriptor):
        """Basic conversion produces a valid OpenAI tool definition."""
        result = converter.convert_descriptor(simple_descriptor)

        assert result["type"] == "function"
        assert "function" in result
        func = result["function"]
        assert func["name"] == "image-resize"
        assert func["description"] == "Resize an image to the specified dimensions"
        assert "parameters" in func

    def test_convert_descriptor_output_format(self, converter, simple_descriptor):
        """Verify exact top-level structure: {"type": "function", "function": {...}}."""
        result = converter.convert_descriptor(simple_descriptor)

        # Only two top-level keys
        assert set(result.keys()) == {"type", "function"}
        assert result["type"] == "function"

        # Function dict must have name, description, parameters
        func = result["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func

        # strict key should NOT be present by default
        assert "strict" not in func

    def test_convert_descriptor_id_normalization(self, converter, simple_descriptor):
        """Dots in module_id are replaced with dashes."""
        result = converter.convert_descriptor(simple_descriptor)
        assert result["function"]["name"] == "image-resize"

    def test_convert_descriptor_id_normalization_deep(self, converter):
        """Multiple dots are all replaced with dashes."""
        descriptor = ModuleDescriptor(
            module_id="comfyui.image.resize.v2",
            description="Deep nested id",
            input_schema={"type": "object", "properties": {}},
            output_schema={},
        )
        result = converter.convert_descriptor(descriptor)
        assert result["function"]["name"] == "comfyui-image-resize-v2"

    def test_convert_descriptor_schema_conversion(self, converter, simple_descriptor):
        """input_schema is properly converted via SchemaConverter."""
        result = converter.convert_descriptor(simple_descriptor)
        params = result["function"]["parameters"]

        # SchemaConverter preserves simple schemas as-is
        assert params["type"] == "object"
        assert "width" in params["properties"]
        assert "height" in params["properties"]
        assert "image_path" in params["properties"]
        assert params["required"] == ["width", "height", "image_path"]

    def test_convert_descriptor_embed_annotations_true(self, converter, simple_descriptor):
        """Annotations suffix is appended to description when embed_annotations=True."""
        result = converter.convert_descriptor(simple_descriptor, embed_annotations=True)
        desc = result["function"]["description"]

        # Should contain the original description
        assert desc.startswith("Resize an image to the specified dimensions")
        # Should contain annotation hints
        assert "[Annotations:" in desc
        assert "idempotent=true" in desc

    def test_convert_descriptor_embed_annotations_false(self, converter, simple_descriptor):
        """No suffix when embed_annotations=False (default)."""
        result = converter.convert_descriptor(simple_descriptor, embed_annotations=False)
        desc = result["function"]["description"]

        assert desc == "Resize an image to the specified dimensions"
        assert "[Annotations:" not in desc

    def test_convert_descriptor_strict_mode(self, converter, simple_descriptor):
        """strict=True adds 'strict': true and sets additionalProperties: false."""
        result = converter.convert_descriptor(simple_descriptor, strict=True)
        func = result["function"]

        assert func["strict"] is True
        params = func["parameters"]
        assert params.get("additionalProperties") is False

    def test_convert_descriptor_strict_mode_all_required(self, converter):
        """In strict mode, all properties appear in the required list (sorted)."""
        descriptor = ModuleDescriptor(
            module_id="test.strict",
            description="Strict test",
            input_schema={
                "type": "object",
                "properties": {
                    "required_field": {"type": "string"},
                    "optional_field": {"type": "integer"},
                },
                "required": ["required_field"],
            },
            output_schema={},
        )
        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]

        # All properties must be in required (sorted alphabetically by to_strict_schema)
        assert params["required"] == ["optional_field", "required_field"]

    def test_convert_descriptor_strict_mode_optional_nullable(self, converter):
        """Optional properties get nullable type in strict mode."""
        descriptor = ModuleDescriptor(
            module_id="test.nullable",
            description="Nullable test",
            input_schema={
                "type": "object",
                "properties": {
                    "required_field": {"type": "string"},
                    "optional_field": {"type": "integer"},
                },
                "required": ["required_field"],
            },
            output_schema={},
        )
        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]

        # required_field should keep its original type
        assert params["properties"]["required_field"]["type"] == "string"

        # optional_field should become nullable
        assert params["properties"]["optional_field"]["type"] == ["integer", "null"]

    def test_convert_descriptor_strict_mode_removes_defaults(self, converter):
        """Strict mode removes default values from properties."""
        descriptor = ModuleDescriptor(
            module_id="test.defaults",
            description="Defaults test",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer", "default": 10},
                },
                "required": ["name"],
            },
            output_schema={},
        )
        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]

        # default should be removed
        assert "default" not in params["properties"]["count"]

    def test_convert_descriptor_strict_mode_nested_objects(self, converter):
        """Strict mode recurses into nested objects."""
        descriptor = ModuleDescriptor(
            module_id="test.nested_strict",
            description="Nested strict test",
            input_schema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["key"],
                    },
                },
                "required": ["config"],
            },
            output_schema={},
        )
        result = converter.convert_descriptor(descriptor, strict=True)
        nested = result["function"]["parameters"]["properties"]["config"]

        assert nested.get("additionalProperties") is False
        assert nested["required"] == ["key", "value"]  # sorted by to_strict_schema
        # value was optional, so it should be nullable
        assert nested["properties"]["value"]["type"] == ["string", "null"]

    def test_convert_descriptor_no_annotations(self, converter, no_annotations_descriptor):
        """Works when annotations is None."""
        result = converter.convert_descriptor(no_annotations_descriptor)

        assert result["type"] == "function"
        func = result["function"]
        assert func["name"] == "text-echo"
        assert func["description"] == "Echo the input text"

    def test_convert_descriptor_no_annotations_embed(self, converter, no_annotations_descriptor):
        """embed_annotations=True with None annotations produces no suffix."""
        result = converter.convert_descriptor(
            no_annotations_descriptor,
            embed_annotations=True,
        )
        desc = result["function"]["description"]
        # AnnotationMapper.to_description_suffix returns "" for None
        assert desc == "Echo the input text"

    def test_convert_descriptor_empty_schema(self, converter, empty_schema_descriptor):
        """Handles empty input_schema (SchemaConverter fills in defaults).
        Post-[SC-10] strict mode (default) also injects additionalProperties:false."""
        result = converter.convert_descriptor(empty_schema_descriptor)
        params = result["function"]["parameters"]

        # SchemaConverter converts {} to {"type": "object", "properties": {}}
        # plus additionalProperties:false in strict mode.
        assert params["type"] == "object"
        assert params["properties"] == {}

    def test_convert_descriptor_destructive(self, converter, destructive_descriptor):
        """Destructive descriptor is correctly converted with annotation embed."""
        result = converter.convert_descriptor(destructive_descriptor, embed_annotations=True)
        desc = result["function"]["description"]

        assert "destructive=true" in desc
        assert "requires_approval=true" in desc


class TestConvertRegistry:
    """Tests for OpenAIConverter.convert_registry."""

    @pytest.fixture
    def converter(self):
        return OpenAIConverter()

    def test_convert_registry_multiple_modules(self, converter):
        """Converts all modules in a registry."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.a",
                    description="Module A",
                    input_schema={
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                    },
                    output_schema={},
                    tags=["core"],
                ),
                ModuleDescriptor(
                    module_id="mod.b",
                    description="Module B",
                    input_schema={
                        "type": "object",
                        "properties": {"y": {"type": "integer"}},
                    },
                    output_schema={},
                    tags=["core"],
                ),
            ]
        )
        results = converter.convert_registry(registry)

        assert len(results) == 2
        names = {r["function"]["name"] for r in results}
        assert names == {"mod-a", "mod-b"}

    def test_convert_registry_empty(self, converter):
        """Returns empty list for empty registry."""
        registry = StubRegistry([])
        results = converter.convert_registry(registry)

        assert results == []

    def test_convert_registry_tag_filter(self, converter):
        """Only includes modules matching tags."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="image.resize",
                    description="Resize",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    tags=["image", "transform"],
                ),
                ModuleDescriptor(
                    module_id="text.echo",
                    description="Echo",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    tags=["text"],
                ),
            ]
        )
        results = converter.convert_registry(registry, tags=["image"])

        assert len(results) == 1
        assert results[0]["function"]["name"] == "image-resize"

    def test_convert_registry_prefix_filter(self, converter):
        """Only includes modules matching prefix."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="image.resize",
                    description="Resize",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    tags=[],
                ),
                ModuleDescriptor(
                    module_id="text.echo",
                    description="Echo",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                    tags=[],
                ),
            ]
        )
        results = converter.convert_registry(registry, prefix="text")

        assert len(results) == 1
        assert results[0]["function"]["name"] == "text-echo"

    def test_convert_registry_skip_none_definition(self, converter):
        """Skips when get_definition returns None (race condition)."""

        class HoleyRegistry:
            """Registry that returns None for some module IDs."""

            def list(self, tags=None, prefix=None):
                return ["mod.exists", "mod.gone"]

            def get_definition(self, module_id):
                if module_id == "mod.exists":
                    return ModuleDescriptor(
                        module_id="mod.exists",
                        description="Exists",
                        input_schema={"type": "object", "properties": {}},
                        output_schema={},
                    )
                return None  # Simulate race condition

        registry = HoleyRegistry()
        results = converter.convert_registry(registry)

        assert len(results) == 1
        assert results[0]["function"]["name"] == "mod-exists"

    def test_convert_registry_passes_embed_and_strict(self, converter):
        """embed_annotations and strict are forwarded to convert_descriptor."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="mod.a",
                    description="Module A",
                    input_schema={
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                    },
                    output_schema={},
                    annotations=ModuleAnnotations(destructive=True),
                ),
            ]
        )
        results = converter.convert_registry(
            registry,
            embed_annotations=True,
            strict=True,
        )

        assert len(results) == 1
        func = results[0]["function"]
        assert func["strict"] is True
        assert "destructive=true" in func["description"]
        assert func["parameters"].get("additionalProperties") is False

    def test_convert_registry_preserves_order(self, converter):
        """Results follow the order returned by registry.list()."""
        registry = StubRegistry(
            [
                ModuleDescriptor(
                    module_id="b.mod",
                    description="B",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                ),
                ModuleDescriptor(
                    module_id="a.mod",
                    description="A",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={},
                ),
            ]
        )
        results = converter.convert_registry(registry)
        names = [r["function"]["name"] for r in results]
        # StubRegistry.list() returns sorted, so a.mod comes before b.mod
        assert names == ["a-mod", "b-mod"]


class TestStrictModeEdgeCases:
    """Tests for strict mode edge cases in _apply_strict_recursive."""

    @pytest.fixture
    def converter(self):
        return OpenAIConverter()

    def test_strict_mode_list_type_already_has_null(self, converter):
        """Strict mode does not duplicate 'null' when type is already a list with null."""
        descriptor = ModuleDescriptor(
            module_id="test.list_null",
            description="Test",
            input_schema={
                "type": "object",
                "properties": {
                    "req_field": {"type": "string"},
                    "opt_field": {"type": ["string", "null"]},
                },
                "required": ["req_field"],
            },
            output_schema={},
        )

        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]
        # opt_field already has null in type list, should not duplicate
        assert params["properties"]["opt_field"]["type"] == ["string", "null"]

    def test_strict_mode_list_type_without_null(self, converter):
        """Strict mode appends null to list type that doesn't have it."""
        descriptor = ModuleDescriptor(
            module_id="test.list_no_null",
            description="Test",
            input_schema={
                "type": "object",
                "properties": {
                    "req_field": {"type": "string"},
                    "opt_field": {"type": ["string", "integer"]},
                },
                "required": ["req_field"],
            },
            output_schema={},
        )

        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]
        assert params["properties"]["opt_field"]["type"] == [
            "string",
            "integer",
            "null",
        ]

    def test_strict_mode_array_items_recursion(self, converter):
        """Strict mode recurses into array items."""
        descriptor = ModuleDescriptor(
            module_id="test.array_recurse",
            description="Test",
            input_schema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "integer", "default": 0},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["items"],
            },
            output_schema={},
        )

        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]
        item_schema = params["properties"]["items"]["items"]
        # Items should have strict mode applied too
        assert item_schema["additionalProperties"] is False
        assert item_schema["required"] == [
            "name",
            "value",
        ]  # sorted by to_strict_schema
        # Default should be removed
        assert "default" not in item_schema["properties"]["value"]

    def test_strict_mode_strips_x_extensions(self, converter):
        """to_strict_schema() strips x-* extension fields."""
        descriptor = ModuleDescriptor(
            module_id="test.extensions",
            description="Test",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "x-llm-description": "A custom hint",
                        "x-sensitive": True,
                    },
                },
                "required": ["name"],
            },
            output_schema={},
        )

        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]
        # x-* fields should be stripped
        assert "x-llm-description" not in params["properties"]["name"]
        assert "x-sensitive" not in params["properties"]["name"]

    def test_strict_mode_promotes_x_llm_description(self, converter):
        """x-llm-description is promoted to description before stripping."""
        descriptor = ModuleDescriptor(
            module_id="test.llm_desc",
            description="Test",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Original description",
                        "x-llm-description": "LLM-optimized description",
                    },
                },
                "required": ["query"],
            },
            output_schema={},
        )

        result = converter.convert_descriptor(descriptor, strict=True)
        params = result["function"]["parameters"]
        # x-llm-description should have been promoted to description
        assert params["properties"]["query"]["description"] == "LLM-optimized description"
        # x-llm-description key itself should be stripped
        assert "x-llm-description" not in params["properties"]["query"]
