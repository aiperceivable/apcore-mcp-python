"""Tests for observability auto-wiring in serve() / APCoreMCP.

Verifies MetricsMiddleware + UsageMiddleware are installed when
``observability=True`` (or ``metrics_collector=True``) and that the existing
custom-MetricsExporter path still works (back-compat).
"""

from __future__ import annotations

from typing import Any

import pytest

from apcore_mcp import APCoreMCP
from apcore_mcp.explorer import create_usage_routes


class _StubRegistry:
    """Minimal Registry used to avoid filesystem side effects."""

    def __init__(self) -> None:
        self._modules: list[str] = []

    def list(self, tags: Any = None, prefix: Any = None) -> list[str]:
        return list(self._modules)

    def get_definition(self, module_id: str) -> Any:
        return None


def _count_middleware(executor: Any, cls_name: str) -> int:
    """Count middleware instances registered on executor matching class name."""
    mws = executor.middlewares
    return sum(1 for m in mws if type(m).__name__ == cls_name)


def test_observability_installs_metrics_and_usage_middleware() -> None:
    mcp = APCoreMCP(_StubRegistry(), observability=True)

    assert _count_middleware(mcp.executor, "MetricsMiddleware") == 1
    assert _count_middleware(mcp.executor, "UsageMiddleware") == 1
    # UsageCollector exposed as attribute.
    assert mcp._usage_collector is not None


def test_metrics_collector_true_installs_metrics_only() -> None:
    mcp = APCoreMCP(_StubRegistry(), metrics_collector=True)

    assert _count_middleware(mcp.executor, "MetricsMiddleware") == 1
    assert _count_middleware(mcp.executor, "UsageMiddleware") == 0
    # Resolved to a real MetricsCollector (not the bool).
    mc = mcp._metrics_collector
    assert mc is not None
    assert mc is not True
    assert hasattr(mc, "export_prometheus")


def test_custom_metrics_exporter_preserved_no_middleware() -> None:
    """Passing a pre-built MetricsExporter without observability installs no middleware."""

    class _CustomExporter:
        def export_prometheus(self) -> str:
            return "custom"

    custom = _CustomExporter()
    mcp = APCoreMCP(_StubRegistry(), metrics_collector=custom)

    assert mcp._metrics_collector is custom
    assert _count_middleware(mcp.executor, "MetricsMiddleware") == 0


def test_create_usage_routes_returns_summary_and_detail() -> None:
    """Explorer usage routes wrap a UsageCollector into two JSON endpoints."""
    from apcore.observability import UsageCollector

    uc = UsageCollector()
    uc.record("m", "caller", 10.0, success=True)
    routes = create_usage_routes(uc, prefix="/explorer")

    assert len(routes) == 2
    paths = {r.path for r in routes}
    assert "/explorer/api/usage" in paths


@pytest.mark.asyncio
async def test_usage_route_returns_json_summary() -> None:
    from apcore.observability import UsageCollector
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    uc = UsageCollector()
    uc.record("mod", "caller-1", 5.0, success=True)
    uc.record("mod", "caller-1", 7.0, success=False)

    app = Starlette(routes=create_usage_routes(uc, prefix="/explorer"))
    with TestClient(app) as client:
        resp = client.get("/explorer/api/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"] == "24h"
        assert any(m["module_id"] == "mod" for m in body["modules"])

        resp2 = client.get("/explorer/api/usage/mod")
        assert resp2.status_code == 200
        detail = resp2.json()
        assert detail["module_id"] == "mod"
        assert detail["call_count"] == 2
