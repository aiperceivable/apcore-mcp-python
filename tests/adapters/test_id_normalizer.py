"""Tests for ModuleIDNormalizer: dot-notation module IDs ↔ OpenAI-compatible names."""

from __future__ import annotations

import re

import pytest

from apcore_mcp.adapters.id_normalizer import ModuleIDNormalizer


class TestModuleIDNormalizer:
    """Test suite for ModuleIDNormalizer."""

    @pytest.fixture
    def normalizer(self) -> ModuleIDNormalizer:
        """Provide a ModuleIDNormalizer instance for tests."""
        return ModuleIDNormalizer()

    def test_normalize_dotted_id(self, normalizer: ModuleIDNormalizer) -> None:
        """Test normalizing a simple dotted module ID."""
        result = normalizer.normalize("image.resize")
        assert result == "image-resize"

    def test_normalize_nested_dots(self, normalizer: ModuleIDNormalizer) -> None:
        """Test normalizing a deeply nested module ID with multiple dots."""
        result = normalizer.normalize("comfyui.image.resize.v2")
        assert result == "comfyui-image-resize-v2"

    def test_normalize_already_valid(self, normalizer: ModuleIDNormalizer) -> None:
        """Test normalizing an ID that's already valid (no dots)."""
        result = normalizer.normalize("simple_module")
        assert result == "simple_module"

    def test_normalize_single_segment(self, normalizer: ModuleIDNormalizer) -> None:
        """Test normalizing a single-segment ID."""
        result = normalizer.normalize("ping")
        assert result == "ping"

    def test_normalize_underscore_preserved(self, normalizer: ModuleIDNormalizer) -> None:
        """Test that underscores are preserved during normalization."""
        result = normalizer.normalize("my_module.sub_module")
        assert result == "my_module-sub_module"

    def test_denormalize_dashed_name(self, normalizer: ModuleIDNormalizer) -> None:
        """Test denormalizing a simple dashed function name."""
        result = normalizer.denormalize("image-resize")
        assert result == "image.resize"

    def test_denormalize_nested(self, normalizer: ModuleIDNormalizer) -> None:
        """Test denormalizing a nested function name with multiple dashes."""
        result = normalizer.denormalize("comfyui-image-resize-v2")
        assert result == "comfyui.image.resize.v2"

    def test_denormalize_no_dashes(self, normalizer: ModuleIDNormalizer) -> None:
        """Test denormalizing a name with no dashes."""
        result = normalizer.denormalize("ping")
        assert result == "ping"

    def test_roundtrip(self, normalizer: ModuleIDNormalizer) -> None:
        """Test that normalize and denormalize are inverse operations."""
        test_ids = [
            "ping",
            "image.resize",
            "comfyui.image.resize.v2",
            "my_module",
            "my_module.sub_module",
            "a.b.c.d.e.f",
        ]

        for module_id in test_ids:
            normalized = normalizer.normalize(module_id)
            denormalized = normalizer.denormalize(normalized)
            assert (
                denormalized == module_id
            ), f"Roundtrip failed for '{module_id}': normalized to '{normalized}', denormalized to '{denormalized}'"

    def test_normalize_empty_string_raises(self, normalizer: ModuleIDNormalizer) -> None:
        """Test normalizing an empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid module ID"):
            normalizer.normalize("")

    def test_normalize_invalid_ids_raise(self, normalizer: ModuleIDNormalizer) -> None:
        """Test that invalid module IDs raise ValueError."""
        invalid_ids = [
            "",
            "ABC.DEF.GHI",
            "lower.UPPER.MiXeD",
            "123starts_with_digit",
            ".leading.dot",
            "trailing.dot.",
            "has spaces",
            "has-dashes",
        ]
        for module_id in invalid_ids:
            with pytest.raises(ValueError, match="Invalid module ID"):
                normalizer.normalize(module_id)

    def test_normalize_result_matches_pattern(self, normalizer: ModuleIDNormalizer) -> None:
        """Test that all normalized results match the OpenAI function name pattern."""
        pattern = re.compile(r"^[a-zA-Z0-9_-]*$")

        test_ids = [
            "ping",
            "image.resize",
            "comfyui.image.resize.v2",
            "my_module",
            "my_module.sub_module",
            "a.b.c.d.e.f",
            "test123.module456",
        ]

        for module_id in test_ids:
            normalized = normalizer.normalize(module_id)
            assert pattern.match(
                normalized
            ), f"Normalized result '{normalized}' from '{module_id}' does not match pattern ^[a-zA-Z0-9_-]*$"


class TestMID5TryDenormalize:
    """[MID-5] try_denormalize is a bijection-guarded inverse of normalize.

    Plain ``denormalize`` is lenient and round-trips only on the image of
    ``normalize``. ``try_denormalize`` returns ``None`` for inputs that are
    not valid pre-images, useful for sanitizing untrusted client input.
    """

    @pytest.fixture
    def normalizer(self) -> ModuleIDNormalizer:
        return ModuleIDNormalizer()

    def test_round_trip_succeeds(self, normalizer: ModuleIDNormalizer) -> None:
        normalized = normalizer.normalize("image.resize")
        assert normalizer.try_denormalize(normalized) == "image.resize"

    @pytest.mark.parametrize(
        "tool_name",
        [
            "Image-Resize",  # uppercase letters
            "--bad",  # leading double-dash -> leading double-dot
            "foo--bar",  # double dash -> double dot (invalid)
            "-foo",  # leading dash -> leading dot (invalid)
            "foo-",  # trailing dash -> trailing dot (invalid)
            "1foo",  # leading digit (invalid)
            "",  # empty (invalid)
        ],
    )
    def test_rejects_invalid_inputs(self, normalizer: ModuleIDNormalizer, tool_name: str) -> None:
        assert normalizer.try_denormalize(tool_name) is None

    def test_accepts_no_dashes(self, normalizer: ModuleIDNormalizer) -> None:
        assert normalizer.try_denormalize("ping") == "ping"

    def test_lenient_denormalize_unchanged(self, normalizer: ModuleIDNormalizer) -> None:
        """Plain denormalize stays lenient — no behavior change for invalid inputs."""
        assert normalizer.denormalize("Image-Resize") == "Image.Resize"
        assert normalizer.denormalize("foo--bar") == "foo..bar"
