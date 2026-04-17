"""Middleware construction from Config Bus `mcp.middleware` entries.

Config Bus schema:

.. code-block:: yaml

    mcp:
      middleware:
        - type: retry
          max_retries: 3
          strategy: exponential
          base_delay_ms: 100
          max_delay_ms: 5000
          jitter: true
        - type: logging
          log_inputs: true
          log_outputs: true
        - type: error_history
          max_entries_per_module: 50
          max_total_entries: 1000

Each entry's ``type`` selects a built-in apcore middleware; remaining keys are
forwarded to the constructor. Unknown ``type`` raises :class:`ValueError` so
misconfiguration fails loudly at startup.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_middleware_from_config(entries: list[dict[str, Any]]) -> list[Any]:
    """Construct apcore middleware instances from Config Bus entries.

    Returns an empty list if apcore is not installed or ``entries`` is empty.
    """
    if not entries:
        return []

    try:
        from apcore import (
            ErrorHistoryMiddleware,
            LoggingMiddleware,
            RetryConfig,
            RetryMiddleware,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Config Bus `mcp.middleware` requires apcore>=0.18 with middleware support"
        ) from exc

    from apcore.observability.error_history import ErrorHistory

    instances: list[Any] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"mcp.middleware[{idx}] must be an object with a 'type' key, got {type(entry).__name__}"
            )
        mw_type = entry.get("type")
        if not mw_type:
            raise ValueError(f"mcp.middleware[{idx}] missing required 'type' key")

        kwargs = {k: v for k, v in entry.items() if k != "type"}

        if mw_type == "retry":
            config = RetryConfig(**kwargs) if kwargs else RetryConfig()
            instances.append(RetryMiddleware(config))
        elif mw_type == "logging":
            instances.append(LoggingMiddleware(**kwargs))
        elif mw_type == "error_history":
            # ErrorHistoryMiddleware wraps an ErrorHistory instance. Accept
            # max_entries_per_module and max_total_entries as shorthand keys
            # that map to ErrorHistory's constructor.
            history_kwargs: dict[str, Any] = {}
            for key in ("max_entries_per_module", "max_total_entries"):
                if key in kwargs:
                    history_kwargs[key] = kwargs.pop(key)
            if kwargs:
                raise ValueError(
                    f"mcp.middleware[{idx}] (error_history) got unexpected keys: {sorted(kwargs)}"
                )
            history = ErrorHistory(**history_kwargs)
            instances.append(ErrorHistoryMiddleware(history))
        else:
            raise ValueError(
                f"mcp.middleware[{idx}] unknown type {mw_type!r}. "
                "Known built-in types: retry, logging, error_history"
            )

    logger.info(
        "Built %d middleware instance(s) from Config Bus: %s",
        len(instances),
        [m.__class__.__name__ for m in instances],
    )
    return instances
