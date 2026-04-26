"""OpenAIConverter: apcore Registry -> OpenAI-compatible tool definitions."""

from __future__ import annotations

from typing import Any

from apcore.schema.strict import _apply_llm_descriptions, to_strict_schema

from apcore_mcp.adapters.annotations import AnnotationMapper
from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer
from apcore_mcp.adapters.schema import SchemaConverter


class OpenAIConverter:
    """Converts apcore Registry modules to OpenAI-compatible tool definitions."""

    def __init__(self) -> None:
        """Initialize with internal SchemaConverter, AnnotationMapper, and ModuleIDNormalizer."""
        self._schema_converter = SchemaConverter()
        self._annotation_mapper = AnnotationMapper()
        self._id_normalizer = ModuleIDNormalizer()

    def convert_registry(
        self,
        registry: Any,
        embed_annotations: bool = False,
        strict: bool = False,
        tags: list[str] | None = None,
        prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert all modules in a Registry to OpenAI tool definitions.

        Uses registry.list(tags=tags, prefix=prefix) for filtering.
        For each module_id, gets descriptor via registry.get_definition(module_id).
        Skips modules where get_definition returns None (race condition).

        Args:
            registry: apcore Registry (duck typed) with list() and get_definition() methods.
            embed_annotations: If True, append annotation hints to descriptions.
            strict: If True, enable OpenAI strict mode on schemas.
            tags: Optional tag filter passed to registry.list().
            prefix: Optional prefix filter passed to registry.list().

        Returns:
            List of OpenAI-compatible tool definition dicts.
        """
        module_ids = registry.list(tags=tags, prefix=prefix)
        tools: list[dict[str, Any]] = []
        # [OC-3] Track normalized names so we can detect collisions.
        # OpenAI function names must be unique post-normalization
        # (dot→hyphen). E.g. `a.b` and `a-b` both normalize to `a-b`;
        # without this guard we'd silently emit two tools with identical
        # function.name, producing undefined OpenAI behavior.
        seen_names: dict[str, str] = {}

        for module_id in module_ids:
            descriptor = registry.get_definition(module_id)
            if descriptor is None:
                continue
            tool = self.convert_descriptor(
                descriptor,
                embed_annotations=embed_annotations,
                strict=strict,
            )
            tool_name = tool["function"]["name"]
            if tool_name in seen_names and seen_names[tool_name] != module_id:
                raise ValueError(
                    f"OpenAI function-name collision: module ids "
                    f"{seen_names[tool_name]!r} and {module_id!r} both normalize "
                    f"to {tool_name!r}. OpenAI requires unique function names; "
                    f"rename one of the modules to avoid the collision."
                )
            seen_names[tool_name] = module_id
            tools.append(tool)

        return tools

    def convert_descriptor(
        self,
        descriptor: Any,
        embed_annotations: bool = False,
        strict: bool = False,
    ) -> dict[str, Any]:
        """Convert a single ModuleDescriptor to OpenAI tool definition.

        Args:
            descriptor: ModuleDescriptor with module_id, description, input_schema,
                and optional annotations.
            embed_annotations: If True, append annotation hints to description.
            strict: If True, enable OpenAI strict mode.

        Returns:
            Dict with structure:
            {
                "type": "function",
                "function": {
                    "name": <normalized_id>,
                    "description": <description [+ annotation suffix]>,
                    "parameters": <converted input_schema>,
                    "strict": True  # only if strict=True
                }
            }
        """
        name = self._id_normalizer.normalize(descriptor.module_id)
        parameters = self._schema_converter.convert_input_schema(descriptor)

        # Build description with optional annotation suffix
        description = descriptor.description
        if embed_annotations:
            suffix = self._annotation_mapper.to_description_suffix(
                descriptor.annotations,
            )
            description += suffix

        # Apply strict mode transformations if requested
        if strict:
            parameters = self._apply_strict_mode(parameters)

        # Build the function dict
        function: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }

        if strict:
            function["strict"] = True

        return {
            "type": "function",
            "function": function,
        }

    def _apply_strict_mode(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Convert schema to OpenAI strict mode via apcore's to_strict_schema().

        Steps:
        1. Deep-copies the input (done by to_strict_schema)
        2. Promotes x-llm-description to description (before stripping)
        3. Strips x-* extensions and default values
        4. Sets additionalProperties: false on all objects
        5. Makes all properties required (sorted alphabetically)
        6. Optional properties become nullable
        7. Recurses into nested objects, array items, oneOf/anyOf/allOf, and $defs

        This matches the behavior of SchemaExporter.export_openai().

        Args:
            schema: JSON Schema dict to transform.

        Returns:
            New schema dict with strict mode applied.
        """
        import copy

        schema = copy.deepcopy(schema)
        _apply_llm_descriptions(schema)
        return to_strict_schema(schema)
