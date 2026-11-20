"""Exercise effective OpenAPI inputs through the public MCP backend."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from apcore import Registry

from apcore_mcp.openapi_backend import openapi_backend

_DOCUMENT = {
    "openapi": "3.1.0",
    "servers": [{"url": "https://example.test"}],
    "components": {
        "parameters": {
            "ItemId": {"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}},
        },
    },
    "paths": {
        "/items/{item_id}": {
            "parameters": [{"$ref": "#/components/parameters/ItemId"}],
            "get": {"operationId": "getItem", "responses": {"200": {"description": "ok"}}},
        },
        "/items": {
            "parameters": [{"name": "preview", "in": "query", "schema": {"type": "boolean"}}],
            "post": {"operationId": "createItem", "responses": {"200": {"description": "ok"}}},
        },
    },
}


def test_selected_unsafe_operation_refuses_before_any_registration() -> None:
    registry = Registry()
    with pytest.raises(ValueError, match=r"create_item.*preview"):
        openapi_backend(_DOCUMENT, registry=registry)
    assert registry.list(visibility=["public", "hidden"]) == []


def test_excluded_operation_leaves_inherited_reference_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = openapi_backend(_DOCUMENT, exclude="^create_item$")
    module = registry.get("get_item")
    assert module is not None
    assert registry.list(visibility=["public", "hidden"]) == ["get_item"]
    assert registry.get_definition("get_item").input_schema["required"] == ["item_id"]
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async def call() -> None:
        async with httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(record),
        ) as client:
            monkeypatch.setattr(httpx, "AsyncClient", lambda **_options: client)
            assert await module.execute({"item_id": "one/two"}) == {"ok": True}

    asyncio.run(call())
    assert [(request.method, request.url.raw_path) for request in requests] == [
        ("GET", b"/items/one%2Ftwo"),
    ]


def test_openapi_requires_toolkit_with_the_shared_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    import apcore_toolkit

    monkeypatch.delattr(apcore_toolkit, "validate_effective_proxy_modules")
    with pytest.raises(RuntimeError, match=r"apcore-toolkit >= 0\.15\.0.*apcore-mcp\[openapi\]"):
        openapi_backend(_DOCUMENT)
