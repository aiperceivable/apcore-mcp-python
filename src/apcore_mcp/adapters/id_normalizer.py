"""ModuleIDNormalizer: dot-notation module IDs ↔ OpenAI-compatible names."""

from __future__ import annotations

from apcore_mcp.constants import MODULE_ID_PATTERN


class ModuleIDNormalizer:
    """Convert between apcore module IDs and OpenAI-compatible function names.

    OpenAI function names must match the pattern ^[a-zA-Z0-9_-]+$.
    apcore module IDs use dot notation (e.g., "image.resize").

    This normalizer provides a bijective mapping:
    - normalize: replace "." with "-"
    - denormalize: replace "-" with "."

    NOTE: This assumes module IDs do not contain literal dashes.
    If they do, the roundtrip will not work correctly.
    This is an acceptable trade-off documented in the tech design.
    """

    def normalize(self, module_id: str) -> str:
        """Convert apcore module_id to OpenAI-compatible function name.

        Replaces '.' with '-' to satisfy ^[a-zA-Z0-9_-]+$ pattern.

        Args:
            module_id: The apcore module ID (e.g., "image.resize")

        Returns:
            OpenAI-compatible function name (e.g., "image-resize")

        Examples:
            >>> normalizer = ModuleIDNormalizer()
            >>> normalizer.normalize("image.resize")
            'image-resize'
            >>> normalizer.normalize("comfyui.image.resize.v2")
            'comfyui-image-resize-v2'
            >>> normalizer.normalize("ping")
            'ping'
        Raises:
            ValueError: If the module_id does not match the required pattern.
        """
        if not MODULE_ID_PATTERN.match(module_id):
            raise ValueError(
                f"Invalid module ID '{module_id}': must match pattern ^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$"
            )
        return module_id.replace(".", "-")

    def denormalize(self, tool_name: str) -> str:
        """Convert OpenAI function name back to apcore module_id.

        Replaces '-' with '.' (inverse of normalize).

        This is the lenient form: it always returns ``tool_name`` with all
        dashes replaced by dots, regardless of whether the result is a
        valid module ID. Use :meth:`try_denormalize` when input may be
        attacker-controlled and you need a bijection guarantee.

        Args:
            tool_name: The OpenAI function name (e.g., "image-resize")

        Returns:
            apcore module ID (e.g., "image.resize")

        Examples:
            >>> normalizer = ModuleIDNormalizer()
            >>> normalizer.denormalize("image-resize")
            'image.resize'
            >>> normalizer.denormalize("comfyui-image-resize-v2")
            'comfyui.image.resize.v2'
            >>> normalizer.denormalize("ping")
            'ping'
        """
        return tool_name.replace("-", ".")

    def try_denormalize(self, normalized: str) -> str | None:
        """Strict inverse of :meth:`normalize` — returns None for non-pre-images.

        [MID-5] Mirrors TypeScript ``tryDenormalize`` and Rust
        ``denormalize_checked``. Runs the dash→dot replacement, then
        validates the result against :data:`MODULE_ID_PATTERN`. Useful for
        sanitizing untrusted client input where ``denormalize`` would
        silently produce a malformed module ID.

        Args:
            normalized: The candidate OpenAI function name.

        Returns:
            The corresponding apcore module ID if it round-trips back to a
            valid module ID, otherwise ``None``.
        """
        candidate = normalized.replace("-", ".")
        if not MODULE_ID_PATTERN.match(candidate):
            return None
        return candidate
