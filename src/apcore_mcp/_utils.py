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


def resolve_executor(
    registry_or_executor: Any,
    *,
    approval_handler: Any = None,
    strategy: Any = None,
    middleware: list[Any] | None = None,
    acl: Any = None,
) -> Any:
    """Get or create an Executor from either a Registry or Executor instance.

    Args:
        registry_or_executor: An apcore Registry or Executor instance.
        approval_handler: Optional approval handler to pass to new Executor instances.
        strategy: Pipeline execution strategy. Valid values: "standard",
            "internal", "testing", "performance", "minimal".
        middleware: Optional list of apcore Middleware instances to register via
            ``executor.use()``. Applied to both newly created and pre-existing
            Executor instances in the order given.
        acl: Optional apcore ``ACL`` instance to install via ``executor.set_acl()``.
            When ``None``, no ACL change is made (a pre-existing ACL on the
            Executor is preserved).
    """
    if hasattr(registry_or_executor, "call_async"):
        executor = registry_or_executor
    else:
        from apcore.executor import Executor

        if isinstance(strategy, str) and strategy not in _VALID_STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy!r}. Valid: {sorted(_VALID_STRATEGIES)}")

        kwargs: dict[str, Any] = {"approval_handler": approval_handler}
        if strategy is not None:
            kwargs["strategy"] = strategy
        executor = Executor(registry_or_executor, **kwargs)

    if middleware:
        if not hasattr(executor, "use"):
            raise RuntimeError("Executor does not support .use() — middleware parameter requires apcore>=0.18")
        for mw in middleware:
            executor.use(mw)

    if acl is not None:
        if not hasattr(executor, "set_acl"):
            raise RuntimeError("Executor does not support .set_acl() — acl parameter requires apcore>=0.18")
        executor.set_acl(acl)

    return executor
