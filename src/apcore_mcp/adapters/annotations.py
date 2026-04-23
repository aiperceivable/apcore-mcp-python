"""AnnotationMapper: apcore ModuleAnnotations → MCP ToolAnnotations."""

from __future__ import annotations

from typing import Any

DEFAULT_ANNOTATIONS = {
    "readonly": False,
    "destructive": False,
    "idempotent": False,
    "requires_approval": False,
    "open_world": True,
    "streaming": False,
    "cacheable": False,
    "cache_ttl": 0,
    "cache_key_fields": None,
    "paginated": False,
    "pagination_style": "cursor",
}


class AnnotationMapper:
    """Maps apcore ModuleAnnotations to MCP ToolAnnotations format.

    This adapter converts between apcore's module annotation system and
    MCP's tool annotation hints, enabling proper tool behavior signaling
    to LLM clients.
    """

    def to_mcp_annotations(self, annotations: Any | None) -> dict[str, Any]:
        """Convert ModuleAnnotations to MCP ToolAnnotations dict.

        Args:
            annotations: ModuleAnnotations instance or None

        Returns:
            Dict with MCP ToolAnnotations fields:
            - read_only_hint: bool | None
            - destructive_hint: bool | None
            - idempotent_hint: bool | None
            - open_world_hint: bool | None
            - title: str | None
        """
        # Default values when annotations is None
        if annotations is None:
            return {
                "read_only_hint": False,
                "destructive_hint": False,
                "idempotent_hint": False,
                "open_world_hint": True,
                "title": None,
            }

        # Map apcore ModuleAnnotations to MCP ToolAnnotations
        return {
            "read_only_hint": annotations.readonly,
            "destructive_hint": annotations.destructive,
            "idempotent_hint": annotations.idempotent,
            "open_world_hint": annotations.open_world,
            "title": None,  # MCP title is not mapped from apcore annotations
        }

    def to_description_suffix(self, annotations: Any | None) -> str:
        """Generate annotation text to append to tool descriptions.

        Produces two sections:
        1. Safety warnings for destructive/approval/external operations.
        2. Machine-readable annotation block for non-default values.

        Args:
            annotations: ModuleAnnotations instance or None

        Returns:
            Formatted string suffix, or empty string if no annotations.
        """
        if annotations is None:
            return ""

        warnings: list[str] = []
        if getattr(annotations, "destructive", False):
            warnings.append(
                "WARNING: DESTRUCTIVE - This operation may irreversibly modify or "
                "delete data. Confirm with user before calling."
            )
        if getattr(annotations, "requires_approval", False):
            warnings.append("REQUIRES APPROVAL: Human confirmation is required before execution.")

        parts: list[str] = []
        if annotations.readonly != DEFAULT_ANNOTATIONS["readonly"]:
            parts.append(f"readonly={str(annotations.readonly).lower()}")
        if annotations.destructive != DEFAULT_ANNOTATIONS["destructive"]:
            parts.append(f"destructive={str(annotations.destructive).lower()}")
        if annotations.idempotent != DEFAULT_ANNOTATIONS["idempotent"]:
            parts.append(f"idempotent={str(annotations.idempotent).lower()}")
        if annotations.requires_approval != DEFAULT_ANNOTATIONS["requires_approval"]:
            parts.append(f"requires_approval={str(annotations.requires_approval).lower()}")
        if annotations.open_world != DEFAULT_ANNOTATIONS["open_world"]:
            parts.append(f"open_world={str(annotations.open_world).lower()}")
        if getattr(annotations, "streaming", False) != DEFAULT_ANNOTATIONS["streaming"]:
            parts.append(f"streaming={str(getattr(annotations, 'streaming', False)).lower()}")
        if getattr(annotations, "cacheable", False) != DEFAULT_ANNOTATIONS["cacheable"]:
            parts.append(f"cacheable={str(getattr(annotations, 'cacheable', False)).lower()}")
        cache_ttl = getattr(annotations, "cache_ttl", DEFAULT_ANNOTATIONS["cache_ttl"])
        if cache_ttl != DEFAULT_ANNOTATIONS["cache_ttl"]:
            parts.append(f"cache_ttl={cache_ttl}")
        cache_key_fields = getattr(annotations, "cache_key_fields", DEFAULT_ANNOTATIONS["cache_key_fields"])
        if cache_key_fields != DEFAULT_ANNOTATIONS["cache_key_fields"] and cache_key_fields:
            parts.append(f"cache_key_fields=[{','.join(cache_key_fields)}]")
        if getattr(annotations, "paginated", False) != DEFAULT_ANNOTATIONS["paginated"]:
            parts.append(f"paginated={str(getattr(annotations, 'paginated', False)).lower()}")
        pagination_style = getattr(annotations, "pagination_style", DEFAULT_ANNOTATIONS["pagination_style"])
        if pagination_style != DEFAULT_ANNOTATIONS["pagination_style"]:
            parts.append(f"pagination_style={pagination_style}")

        sections: list[str] = []
        if warnings:
            sections.append("\n".join(warnings))
        if parts:
            sections.append(f"[Annotations: {', '.join(parts)}]")

        # F-041: Extract mcp_ prefixed keys from extra
        extra = getattr(annotations, "extra", None)
        if extra and isinstance(extra, dict):
            for key in sorted(extra.keys()):
                if key.startswith("mcp_") and isinstance(extra[key], str):
                    stripped = key[4:]  # Remove "mcp_" prefix
                    sections.append(f"{stripped}: {extra[key]}")

        if not sections:
            return ""

        return "\n\n" + "\n\n".join(sections)

    def has_requires_approval(self, annotations: Any | None) -> bool:
        """Check if module requires human approval before execution.

        Args:
            annotations: ModuleAnnotations instance or None

        Returns:
            True if requires_approval is set, False otherwise
        """
        if annotations is None:
            return False

        return annotations.requires_approval
