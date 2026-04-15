"""Tests for SchemaConverter."""

from __future__ import annotations

import pytest

from apcore_mcp.adapters.schema import SchemaConverter


class TestSchemaConverter:
    """Test suite for SchemaConverter."""

    @pytest.fixture
    def converter(self):
        """Create a SchemaConverter instance for tests (non-strict for legacy assertions)."""
        return SchemaConverter(strict=False)

    @pytest.fixture
    def strict_converter(self):
        """Create a strict-mode SchemaConverter (MCP default)."""
        return SchemaConverter(strict=True)

    def test_strict_default_is_true(self):
        """Default SchemaConverter should be strict=True for MCP."""
        c = SchemaConverter()
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_default",
            description="",
            input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            output_schema={},
        )
        result = c.convert_input_schema(descriptor)
        assert result["additionalProperties"] is False

    def test_strict_injects_root_additional_properties_false(self, strict_converter):
        """Root object-typed schema gets additionalProperties: false."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_root",
            description="",
            input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["additionalProperties"] is False

    def test_strict_injects_nested_additional_properties_false(self, strict_converter):
        """Nested object-typed subschemas also get additionalProperties: false."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_nested",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "outer": {
                        "type": "object",
                        "properties": {
                            "inner": {
                                "type": "object",
                                "properties": {"x": {"type": "integer"}},
                            },
                        },
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["additionalProperties"] is False
        assert result["properties"]["outer"]["additionalProperties"] is False
        assert result["properties"]["outer"]["properties"]["inner"]["additionalProperties"] is False

    def test_strict_preserves_user_additional_properties_true(self, strict_converter):
        """When the module already set additionalProperties, user intent wins."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_preserve",
            description="",
            input_schema={
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "bag": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "properties": {},
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["additionalProperties"] is True
        assert result["properties"]["bag"]["additionalProperties"] == {"type": "string"}

    def test_convert_simple_schema(self, converter, simple_descriptor):
        """Test that a simple schema is preserved exactly."""
        result = converter.convert_input_schema(simple_descriptor)

        # Should preserve the schema exactly
        assert result == {
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
        }

    def test_convert_empty_schema(self, converter, empty_schema_descriptor):
        """Test that an empty schema returns object with properties."""
        result = converter.convert_input_schema(empty_schema_descriptor)

        # Empty schema should become {"type": "object", "properties": {}}
        assert result == {"type": "object", "properties": {}}

    def test_convert_nested_schema_with_refs(self, converter, nested_schema_descriptor):
        """Test that nested schema with $defs and $ref gets inlined."""
        result = converter.convert_input_schema(nested_schema_descriptor)

        # $defs should be removed
        assert "$defs" not in result

        # $ref should be inlined
        assert "$ref" not in str(result)

        # The Step definition should be inlined into the items
        assert result["properties"]["steps"]["items"] == {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["name"],
        }

        # Other properties should be preserved
        assert result["properties"]["workflow_name"] == {"type": "string"}
        assert result["properties"]["dry_run"] == {"type": "boolean", "default": False}
        assert result["required"] == ["workflow_name", "steps"]

    def test_convert_all_types(self, converter, all_types_descriptor):
        """Test that all JSON Schema types are preserved."""
        result = converter.convert_input_schema(all_types_descriptor)

        # All types should be preserved
        assert result["properties"]["str_field"] == {"type": "string"}
        assert result["properties"]["int_field"] == {"type": "integer"}
        assert result["properties"]["num_field"] == {"type": "number"}
        assert result["properties"]["bool_field"] == {"type": "boolean"}
        assert result["properties"]["null_field"] == {"type": "null"}
        assert result["properties"]["arr_field"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert result["properties"]["obj_field"] == {
            "type": "object",
            "properties": {"nested": {"type": "string"}},
        }

    def test_convert_output_schema(self, converter, simple_descriptor):
        """Test that output_schema conversion works the same as input."""
        result = converter.convert_output_schema(simple_descriptor)

        assert result == {
            "type": "object",
            "properties": {
                "output_path": {"type": "string"},
                "original_size": {"type": "array", "items": {"type": "integer"}},
                "new_size": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["output_path"],
        }

    def test_ensure_object_type_missing(self, converter):
        """Test that schema without type gets type: object added."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.missing_type",
            description="Test missing type",
            input_schema={
                "properties": {
                    "field": {"type": "string"},
                }
            },
            output_schema={},
        )

        result = converter.convert_input_schema(descriptor)

        # Should add type: object
        assert result["type"] == "object"
        assert result["properties"]["field"] == {"type": "string"}

    def test_ensure_object_type_present(self, converter, simple_descriptor):
        """Test that schema with type: object is unchanged."""
        result = converter.convert_input_schema(simple_descriptor)

        # Should still have type: object
        assert result["type"] == "object"

    def test_inline_nested_ref(self, converter):
        """Test that deeply nested $ref chains are resolved."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.nested_refs",
            description="Test nested refs",
            input_schema={
                "type": "object",
                "$defs": {
                    "A": {
                        "type": "object",
                        "properties": {
                            "b": {"$ref": "#/$defs/B"},
                        },
                    },
                    "B": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                    },
                },
                "properties": {
                    "root": {"$ref": "#/$defs/A"},
                },
            },
            output_schema={},
        )

        result = converter.convert_input_schema(descriptor)

        # $defs should be removed
        assert "$defs" not in result

        # All $refs should be inlined
        assert "$ref" not in str(result)

        # Verify nested structure is correctly inlined
        assert result["properties"]["root"] == {
            "type": "object",
            "properties": {
                "b": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                    },
                },
            },
        }

    def test_schema_with_unicode(self, converter):
        """Test that descriptions with unicode are preserved."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.unicode",
            description="Test unicode",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "User's name with émojis 🎉",
                    },
                },
            },
            output_schema={},
        )

        result = converter.convert_input_schema(descriptor)

        # Unicode should be preserved
        assert result["properties"]["name"]["description"] == "User's name with émojis 🎉"

    def test_schema_passthrough(self, converter, simple_descriptor):
        """Test that normal schema without $ref passes through unchanged."""
        result = converter.convert_input_schema(simple_descriptor)

        # Should be identical to the original input_schema
        assert result == simple_descriptor.input_schema

        # Verify it's a deep copy, not the same object
        assert result is not simple_descriptor.input_schema

    def test_circular_ref_raises_value_error(self, converter):
        """Test that circular $ref raises ValueError."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.circular",
            description="Test circular ref",
            input_schema={
                "type": "object",
                "$defs": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "child": {"$ref": "#/$defs/Node"},
                        },
                    },
                },
                "properties": {
                    "root": {"$ref": "#/$defs/Node"},
                },
            },
            output_schema={},
        )

        with pytest.raises(ValueError, match="Circular \\$ref detected"):
            converter.convert_input_schema(descriptor)

    def test_unsupported_ref_format_raises(self, converter):
        """Test that an unsupported $ref format raises ValueError."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.bad_ref",
            description="Test bad ref",
            input_schema={
                "type": "object",
                "$defs": {"Foo": {"type": "string"}},
                "properties": {
                    "x": {"$ref": "http://example.com/schema"},
                },
            },
            output_schema={},
        )

        with pytest.raises(ValueError, match="Unsupported \\$ref format"):
            converter.convert_input_schema(descriptor)

    def test_missing_ref_definition_raises(self, converter):
        """Test that a $ref to a missing definition raises KeyError."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.missing_def",
            description="Test missing def",
            input_schema={
                "type": "object",
                "$defs": {},
                "properties": {
                    "x": {"$ref": "#/$defs/NonExistent"},
                },
            },
            output_schema={},
        )

        with pytest.raises(KeyError, match="Definition not found"):
            converter.convert_input_schema(descriptor)

    def test_schema_with_list_items(self, converter):
        """Test that list values in schemas are handled correctly."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.list_items",
            description="Test list items",
            input_schema={
                "type": "object",
                "$defs": {
                    "Item": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/Item"},
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            output_schema={},
        )

        result = converter.convert_input_schema(descriptor)

        assert "$defs" not in result
        assert result["properties"]["items"]["items"] == {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }

    def test_strict_nullable_object_union_type(self, strict_converter):
        """type: ['object', 'null'] is recognized as object-like."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_nullable",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "nullable": {
                        "type": ["object", "null"],
                        "properties": {"x": {"type": "string"}},
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["properties"]["nullable"]["additionalProperties"] is False

    def test_strict_properties_without_type(self, strict_converter):
        """Schema with 'properties' but no 'type' gets additionalProperties: false."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_no_type",
            description="",
            input_schema={
                "properties": {
                    "nested": {
                        "properties": {"x": {"type": "string"}},
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["additionalProperties"] is False
        assert result["properties"]["nested"]["additionalProperties"] is False

    def test_strict_does_not_mutate_enum_literal_objects(self, strict_converter):
        """enum: [{...}] member objects must not be mutated."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_enum",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "choice": {
                        "enum": [{"x": 1}, {"y": 2}],
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["properties"]["choice"]["enum"] == [{"x": 1}, {"y": 2}]
        for member in result["properties"]["choice"]["enum"]:
            assert "additionalProperties" not in member

    def test_strict_does_not_mutate_const_literal_object(self, strict_converter):
        """const: {...} must not get additionalProperties injected."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_const",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "c": {"const": {"a": 1}},
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        assert result["properties"]["c"]["const"] == {"a": 1}
        assert "additionalProperties" not in result["properties"]["c"]["const"]

    def test_strict_does_not_mutate_examples_and_default(self, strict_converter):
        """examples: [...] and default: {...} literal objects are preserved."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_examples",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "e": {
                        "type": "object",
                        "properties": {"v": {"type": "string"}},
                        "examples": [{"v": "x"}],
                        "default": {"v": "y"},
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        e = result["properties"]["e"]
        assert e["examples"] == [{"v": "x"}]
        assert e["default"] == {"v": "y"}
        assert "additionalProperties" not in e["examples"][0]
        assert "additionalProperties" not in e["default"]

    def test_strict_oneof_object_branch(self, strict_converter):
        """oneOf: [{type:object,...}, {type:null}] — object branch strict, null untouched."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.strict_oneof",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "u": {
                        "oneOf": [
                            {"type": "object", "properties": {"x": {"type": "string"}}},
                            {"type": "null"},
                        ],
                    },
                },
            },
            output_schema={},
        )
        result = strict_converter.convert_input_schema(descriptor)
        branches = result["properties"]["u"]["oneOf"]
        assert branches[0]["additionalProperties"] is False
        assert "additionalProperties" not in branches[1]

    def test_ensure_object_type_with_mismatched_type(self, converter):
        """Test schema with properties but non-object type gets corrected."""
        from tests.conftest import ModuleDescriptor

        descriptor = ModuleDescriptor(
            module_id="test.mismatch",
            description="Mismatched type",
            input_schema={
                "type": "string",
                "properties": {"field": {"type": "string"}},
            },
            output_schema={},
        )

        result = converter.convert_input_schema(descriptor)
        assert result["type"] == "object"
