"""SchemaConverter: apcore schemas → MCP inputSchema / OpenAI parameters."""

from __future__ import annotations

import copy
from typing import Any

_MAX_REF_DEPTH = 32

# Keywords whose values are subschemas (or collections of subschemas) and
# should be walked when injecting additionalProperties: false. Keys like
# ``enum``, ``const``, ``examples``, ``default``, ``required`` hold literal
# data and must NOT be recursed into.
_SCHEMA_CHILD_DICT_KEYS = ("properties", "patternProperties", "$defs", "definitions")
_SCHEMA_CHILD_LIST_KEYS = ("oneOf", "anyOf", "allOf", "prefixItems")
_SCHEMA_CHILD_SCHEMA_KEYS = ("items", "not", "if", "then", "else", "contains", "propertyNames")


class SchemaConverter:
    """Converts apcore ModuleDescriptor schemas to MCP-compatible schemas.

    Key transformations:
    - Empty schemas → {"type": "object", "properties": {}}
    - Schemas with $defs and $ref → inline all refs, strip $defs
    - Ensures all schemas have "type": "object" at the root level
    - When ``strict=True`` (default), injects ``additionalProperties: false``
      on every object-typed node that doesn't already set it.  Existing
      user-supplied ``additionalProperties`` values are preserved.
    - Returns deep copies (doesn't modify original schemas)
    """

    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict

    def convert_input_schema(self, descriptor: Any) -> dict[str, Any]:
        """Convert apcore ModuleDescriptor.input_schema to MCP inputSchema.

        Args:
            descriptor: ModuleDescriptor with input_schema attribute

        Returns:
            MCP-compatible schema dict with $refs inlined and $defs removed
        """
        schema = descriptor.input_schema
        return self._convert_schema(schema)

    def convert_output_schema(self, descriptor: Any) -> dict[str, Any]:
        """Convert apcore ModuleDescriptor.output_schema.

        Args:
            descriptor: ModuleDescriptor with output_schema attribute

        Returns:
            MCP-compatible schema dict with $refs inlined and $defs removed
        """
        schema = descriptor.output_schema
        return self._convert_schema(schema)

    def _convert_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Convert a schema, applying all transformations.

        Args:
            schema: JSON Schema dict to convert

        Returns:
            Converted schema with $refs inlined, $defs removed, and type ensured
        """
        # Make a deep copy to avoid modifying the original
        schema = copy.deepcopy(schema)

        # Handle empty schema
        if not schema:
            result: dict[str, Any] = {"type": "object", "properties": {}}
            # [SC-10] Spec mandates additionalProperties:false on objects
            # in strict mode, INCLUDING the empty-schema short-circuit.
            # Pre-fix Python skipped strict on this branch; TS+Rust both
            # inject. Now uniform.
            if self._strict:
                result["additionalProperties"] = False
            return result

        # Inline $refs if present
        if "$defs" in schema:
            defs = schema["$defs"]
            schema = self._inline_refs(schema, defs)
            # Remove $defs from the final schema
            schema.pop("$defs", None)

        # Ensure schema has type: object
        schema = self._ensure_object_type(schema)

        if self._strict:
            self._inject_additional_properties_false(schema)

        return schema

    def _inject_additional_properties_false(self, node: Any) -> None:
        """Walk *node* and set ``additionalProperties: false`` on every
        object-typed subschema that doesn't already define the key.
        User intent wins: existing ``additionalProperties`` values (including
        ``True``) are left untouched.

        Recursion is narrowed to known subschema-bearing keywords so we do
        NOT descend into literal-data keys like ``enum``, ``const``,
        ``examples``, ``default``, ``required``.
        """
        if not isinstance(node, dict):
            return

        node_type = node.get("type")
        type_is_object = node_type == "object" or (isinstance(node_type, list) and "object" in node_type)
        # Treat schema as object-like when it has "properties" and no
        # conflicting non-object scalar type.
        has_properties = "properties" in node
        non_object_scalar = isinstance(node_type, str) and node_type != "object"
        is_object = type_is_object or (has_properties and not non_object_scalar)

        if is_object and "additionalProperties" not in node:
            node["additionalProperties"] = False

        # Recurse only into known subschema-bearing keywords.
        for key in _SCHEMA_CHILD_DICT_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                for sub in child.values():
                    self._inject_additional_properties_false(sub)

        for key in _SCHEMA_CHILD_LIST_KEYS:
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    self._inject_additional_properties_false(item)

        for key in _SCHEMA_CHILD_SCHEMA_KEYS:
            child = node.get(key)
            if isinstance(child, dict):
                self._inject_additional_properties_false(child)
            elif isinstance(child, list):
                # ``items`` may be a list of schemas (legacy tuple validation)
                for item in child:
                    self._inject_additional_properties_false(item)

        # additionalProperties itself may be a schema dict.
        ap = node.get("additionalProperties")
        if isinstance(ap, dict):
            self._inject_additional_properties_false(ap)

    def _inline_refs(
        self,
        schema: dict[str, Any],
        defs: dict[str, Any],
        _seen: set[str] | None = None,
        _depth: int = 0,
    ) -> dict[str, Any]:
        """Recursively inline all $ref references, removing $defs.

        Args:
            schema: Schema dict that may contain $refs
            defs: Dictionary of definitions from $defs
            _seen: Internal set tracking visited $ref paths to prevent
                infinite recursion on circular references.
            _depth: Current recursion depth for safety limit.

        Returns:
            Schema with all $refs replaced by their definitions

        Raises:
            ValueError: If a circular $ref is detected or depth exceeds limit.
        """
        if _depth > _MAX_REF_DEPTH:
            raise ValueError(f"$ref resolution exceeded maximum depth of {_MAX_REF_DEPTH}")

        if _seen is None:
            _seen = set()

        if isinstance(schema, dict):
            # If this is a $ref, resolve it
            if "$ref" in schema:
                ref_path = schema["$ref"]
                if ref_path in _seen:
                    raise ValueError(f"Circular $ref detected: {ref_path}")
                _seen = _seen | {ref_path}
                resolved = self._resolve_ref(ref_path, defs)
                # Recursively inline refs in the resolved schema
                return self._inline_refs(resolved, defs, _seen, _depth + 1)

            # Otherwise, recursively process all values
            result = {}
            for key, value in schema.items():
                if key == "$defs":
                    # Skip $defs, we'll remove it later
                    continue
                result[key] = self._inline_refs(value, defs, _seen, _depth + 1)
            return result
        elif isinstance(schema, list):
            # Recursively process list items
            return [self._inline_refs(item, defs, _seen, _depth + 1) for item in schema]
        else:
            # Primitive value, return as-is
            return schema

    def _resolve_ref(self, ref_path: str, defs: dict[str, Any]) -> dict[str, Any]:
        """Resolve a single $ref path against $defs.

        Args:
            ref_path: JSON Schema $ref path like "#/$defs/Step"
            defs: Dictionary of definitions

        Returns:
            The resolved schema definition (deep copy)

        Raises:
            ValueError: If the $ref path is invalid or not found
        """
        # Parse the $ref path
        # Expected format: "#/$defs/DefinitionName"
        if not ref_path.startswith("#/$defs/"):
            raise ValueError(f"Unsupported $ref format: {ref_path}")

        # Extract the definition name
        def_name = ref_path[8:]  # Remove "#/$defs/"

        if def_name not in defs:
            raise KeyError(f"Definition not found: {def_name}")

        # Return a deep copy to avoid circular reference issues
        return copy.deepcopy(defs[def_name])

    def _ensure_object_type(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Ensure schema has type: object with properties.

        Args:
            schema: Schema dict that may be missing "type"

        Returns:
            Schema with "type": "object" guaranteed at root level
        """
        # If schema doesn't have a type, add type: object
        if "type" not in schema:
            schema["type"] = "object"

        # If schema has properties but no object type, force object — but only
        # when the existing type does not already include "object".  This
        # correctly handles both the scalar form ("string", "integer") AND
        # the list form (["object", "null"]) so that nullable-object schemas
        # are not downgraded from ["object","null"] to bare "object".
        node_type = schema.get("type")
        type_is_object = node_type == "object" or (isinstance(node_type, list) and "object" in node_type)
        if "properties" in schema and not type_is_object:
            schema["type"] = "object"

        return schema
