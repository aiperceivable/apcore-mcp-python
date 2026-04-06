"""Internal utility functions for apcore-mcp."""

from __future__ import annotations

from typing import Any


def resolve_registry(registry_or_executor: Any) -> Any:
    """Extract Registry from either a Registry or Executor instance."""
    if hasattr(registry_or_executor, "registry"):
        # It's an Executor — get its registry
        return registry_or_executor.registry
    # Assume it's a Registry
    return registry_or_executor


_VALID_STRATEGIES = {"standard", "internal", "testing", "performance", "minimal"}


def resolve_executor(registry_or_executor: Any, *, approval_handler: Any = None, strategy: Any = None) -> Any:
    """Get or create an Executor from either a Registry or Executor instance.

    Args:
        registry_or_executor: An apcore Registry or Executor instance.
        approval_handler: Optional approval handler to pass to new Executor instances.
        strategy: Pipeline execution strategy. Valid values: "standard",
            "internal", "testing", "performance", "minimal".
    """
    if hasattr(registry_or_executor, "call_async"):
        # Already an Executor
        return registry_or_executor
    # It's a Registry — create a default Executor
    from apcore.executor import Executor

    if strategy is not None and strategy not in _VALID_STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy!r}. Valid: {sorted(_VALID_STRATEGIES)}")

    kwargs: dict[str, Any] = {"approval_handler": approval_handler}
    if strategy is not None:
        kwargs["strategy"] = strategy
    return Executor(registry_or_executor, **kwargs)
