"""Unit tests for AnnotationMapper."""

from __future__ import annotations

import pytest

from apcore_mcp.adapters.annotations import AnnotationMapper
from tests.conftest import ModuleAnnotations


class TestAnnotationMapper:
    """Test suite for AnnotationMapper."""

    @pytest.fixture
    def mapper(self) -> AnnotationMapper:
        """Create an AnnotationMapper instance for testing."""
        return AnnotationMapper()

    def test_readonly_annotation(self, mapper: AnnotationMapper) -> None:
        """Test readonly annotation maps to readOnlyHint=True."""
        annotations = ModuleAnnotations(readonly=True)
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
            "title": None,
        }

    def test_destructive_annotation(self, mapper: AnnotationMapper) -> None:
        """Test destructive annotation maps to destructiveHint=True."""
        annotations = ModuleAnnotations(destructive=True)
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
            "title": None,
        }

    def test_idempotent_annotation(self, mapper: AnnotationMapper) -> None:
        """Test idempotent annotation maps to idempotentHint=True."""
        annotations = ModuleAnnotations(idempotent=True)
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": None,
        }

    def test_open_world_false(self, mapper: AnnotationMapper) -> None:
        """Test open_world=False maps to openWorldHint=False."""
        annotations = ModuleAnnotations(open_world=False)
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
            "title": None,
        }

    def test_all_defaults(self, mapper: AnnotationMapper) -> None:
        """Test default ModuleAnnotations maps to default MCP annotations."""
        annotations = ModuleAnnotations()
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
            "title": None,
        }

    def test_none_annotations(self, mapper: AnnotationMapper) -> None:
        """Test None annotations uses default values."""
        result = mapper.to_mcp_annotations(None)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
            "title": None,
        }

    def test_combined_annotations(self, mapper: AnnotationMapper) -> None:
        """Test multiple annotations combine correctly."""
        annotations = ModuleAnnotations(
            destructive=True,
            requires_approval=True,
            open_world=False,
        )
        result = mapper.to_mcp_annotations(annotations)

        assert result == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
            "title": None,
        }

    def test_has_requires_approval_true(self, mapper: AnnotationMapper) -> None:
        """Test has_requires_approval returns True when requires_approval=True."""
        annotations = ModuleAnnotations(requires_approval=True)
        assert mapper.has_requires_approval(annotations) is True

    def test_has_requires_approval_false(self, mapper: AnnotationMapper) -> None:
        """Test has_requires_approval returns False for default annotations."""
        annotations = ModuleAnnotations()
        assert mapper.has_requires_approval(annotations) is False

    def test_has_requires_approval_none(self, mapper: AnnotationMapper) -> None:
        """Test has_requires_approval returns False for None annotations."""
        assert mapper.has_requires_approval(None) is False

    def test_description_suffix_with_annotations(self, mapper: AnnotationMapper) -> None:
        """Test description suffix embeds warnings and non-default annotations."""
        annotations = ModuleAnnotations(
            destructive=True,
            readonly=False,
            idempotent=True,
            requires_approval=True,
            open_world=False,
        )
        result = mapper.to_description_suffix(annotations)

        # Starts with newlines, then safety warnings
        assert result.startswith("\n\n")
        assert "WARNING: DESTRUCTIVE" in result
        assert "REQUIRES APPROVAL" in result
        # Annotation block present
        assert "[Annotations: " in result
        assert result.endswith("]")
        assert "destructive=true" in result.lower()
        assert "idempotent=true" in result.lower()
        assert "requires_approval=true" in result.lower()
        assert "open_world=false" in result.lower()
        # readonly=False is the default, so it should NOT appear
        assert "readonly" not in result.lower()

    def test_description_suffix_empty(self, mapper: AnnotationMapper) -> None:
        """Test description suffix returns empty string for None annotations."""
        result = mapper.to_description_suffix(None)
        assert result == ""

    def test_description_suffix_defaults(self, mapper: AnnotationMapper) -> None:
        """Test description suffix with all-default annotations returns empty string."""
        annotations = ModuleAnnotations()
        result = mapper.to_description_suffix(annotations)

        # All-default annotations should return empty string
        assert result == ""

    # ── Streaming annotation ────────────────────────────────────────────

    def test_default_annotations_contains_streaming(self) -> None:
        """DEFAULT_ANNOTATIONS contains streaming key."""
        from apcore_mcp.adapters.annotations import DEFAULT_ANNOTATIONS

        assert "streaming" in DEFAULT_ANNOTATIONS
        assert DEFAULT_ANNOTATIONS["streaming"] is False

    def test_description_suffix_includes_streaming_true(self, mapper: AnnotationMapper) -> None:
        """to_description_suffix includes streaming=true when annotation has streaming=True."""
        annotations = ModuleAnnotations(streaming=True)
        result = mapper.to_description_suffix(annotations)

        assert "streaming=true" in result.lower()

    def test_description_suffix_includes_cacheable_true(self, mapper: AnnotationMapper) -> None:
        """to_description_suffix includes cacheable=true when annotation has cacheable=True."""
        annotations = ModuleAnnotations(cacheable=True)
        result = mapper.to_description_suffix(annotations)

        assert "cacheable=true" in result.lower()

    def test_description_suffix_includes_paginated_true(self, mapper: AnnotationMapper) -> None:
        """to_description_suffix includes paginated=true when annotation has paginated=True."""
        annotations = ModuleAnnotations(paginated=True)
        result = mapper.to_description_suffix(annotations)

        assert "paginated=true" in result.lower()

    def test_description_suffix_excludes_cacheable_default(self, mapper: AnnotationMapper) -> None:
        """to_description_suffix omits cacheable when it is the default (False)."""
        annotations = ModuleAnnotations()
        result = mapper.to_description_suffix(annotations)

        assert "cacheable" not in result

    def test_description_suffix_excludes_paginated_default(self, mapper: AnnotationMapper) -> None:
        """to_description_suffix omits paginated when it is the default (False)."""
        annotations = ModuleAnnotations()
        result = mapper.to_description_suffix(annotations)

        assert "paginated" not in result

    def test_to_mcp_annotations_unchanged_no_streaming(self, mapper: AnnotationMapper) -> None:
        """to_mcp_annotations does not include streaming (MCP ToolAnnotations has no streaming field)."""
        annotations = ModuleAnnotations()
        result = mapper.to_mcp_annotations(annotations)

        assert "streaming" not in result

    def test_am_l1_extras_appended_with_single_newline(self, mapper: AnnotationMapper) -> None:
        """[AM-L1] mcp_-prefixed extras are appended after the [Annotations: ...]
        block separated by ONE newline (matches TS+Rust). Pre-fix Python emitted
        each extra as its own section separated by ``\\n\\n``.
        """
        # The conftest ModuleAnnotations stub is a frozen dataclass without an
        # `extra` field — use a SimpleNamespace so the duck-typed `getattr(_, "extra")`
        # path inside AnnotationMapper sees a dict.
        from types import SimpleNamespace

        annotations = SimpleNamespace(
            readonly=False,
            destructive=True,
            idempotent=False,
            requires_approval=False,
            open_world=True,
            streaming=False,
            cacheable=False,
            cache_ttl=0,
            cache_key_fields=None,
            paginated=False,
            pagination_style="cursor",
            extra={"mcp_category": "image", "mcp_cost": "high", "internal_flag": "x"},
        )
        result = mapper.to_description_suffix(annotations)

        # The [Annotations: ...] block is followed by extras with ONE newline
        # between them and ONE newline between consecutive extras.
        assert "[Annotations: destructive=true]\ncategory: image\ncost: high" in result
        # No double newline between the [Annotations: ...] block and the first extra
        assert "[Annotations: destructive=true]\n\ncategory:" not in result
        # internal_flag (no mcp_ prefix) is NOT surfaced
        assert "internal_flag" not in result
