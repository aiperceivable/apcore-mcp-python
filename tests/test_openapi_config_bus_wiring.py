"""Regression tests for the `mcp.openapi` Config-Bus-only backend route.

PRD F-054's Description and Acceptance Criterion 1 state that OpenAPI is
"Reached from the Config Bus (`mcp.openapi`), from seven CLI flags, and from
`APCoreMCP.from_openapi(...)`" and that "`--from-openapi <url|path>` and
`mcp.openapi.spec` both start a server". Before this fix, `mcp.openapi` was
registered as a Config Bus namespace default but nothing ever read it back —
the Config-Bus route was documented and silently absent. These tests pin
the fix: `mcp.openapi.spec` alone, with no CLI flag and no explicit
`from_openapi()`/`openapi_backend()` call, is sufficient.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apcore_mcp.apcore_mcp import APCoreMCP
from apcore_mcp.openapi_backend import build_openapi_backend_from_config


class _FakeConfig:
    """Minimal ``apcore.Config`` stand-in exposing ``.get(key)``."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def get(self, key: str) -> Any:
        return self._store.get(key)


_PETSTORE = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets": {
            "get": {"operationId": "listPets", "responses": {"200": {"description": "ok"}}},
        },
    },
}


class TestBuildOpenapiBackendFromConfig:
    def test_falsy_config_returns_none(self) -> None:
        assert build_openapi_backend_from_config(None) is None
        assert build_openapi_backend_from_config({}) is None

    def test_non_mapping_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            build_openapi_backend_from_config("nope")  # type: ignore[arg-type]

    def test_missing_spec_raises(self) -> None:
        with pytest.raises(ValueError, match="mcp.openapi.spec is required"):
            build_openapi_backend_from_config({"prefix": "x"})

    def test_builds_registry_from_parsed_document(self) -> None:
        registry = build_openapi_backend_from_config({"spec": _PETSTORE})
        assert registry is not None
        assert "list_pets" in registry.list(visibility=["public", "hidden"])

    def test_acknowledge_unapproved_writes_reaches_openapi_backend(self, caplog: pytest.LogCaptureFixture) -> None:
        write_doc = {
            **_PETSTORE,
            "paths": {
                "/pets": {
                    "post": {"operationId": "createPet", "responses": {"200": {"description": "ok"}}},
                },
            },
        }
        with caplog.at_level("WARNING"):
            build_openapi_backend_from_config({"spec": write_doc, "acknowledge_unapproved_writes": True})
        assert not any("will not fire for any of them" in r.message for r in caplog.records)


class TestApcoreMcpOpenapiConfigBusOnly:
    """`APCoreMCP(None, ...)` resolves the backend from `mcp.openapi` alone."""

    def test_none_backend_with_openapi_config_builds_server(self) -> None:
        fake_cfg = _FakeConfig({"mcp.openapi": {"spec": _PETSTORE}})
        with patch("apcore.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = fake_cfg
            mcp = APCoreMCP(None, name="test")
        assert "list_pets" in mcp.tools

    def test_none_backend_with_no_config_raises(self) -> None:
        fake_cfg = _FakeConfig({})
        with patch("apcore.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = fake_cfg
            with pytest.raises(ValueError, match="extensions_dir_or_backend is required"):
                APCoreMCP(None, name="test")

    def test_explicit_backend_plus_openapi_config_requires_prefix(self) -> None:
        from apcore import Registry

        fake_cfg = _FakeConfig({"mcp.openapi": {"spec": _PETSTORE}})
        with patch("apcore.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = fake_cfg
            with pytest.raises(ValueError, match="mcp.openapi.prefix is required"):
                APCoreMCP(Registry(), name="test")

    def test_explicit_backend_plus_openapi_config_with_prefix_unions(self) -> None:
        from apcore import Registry

        fake_cfg = _FakeConfig({"mcp.openapi": {"spec": _PETSTORE, "prefix": "petstore"}})
        with patch("apcore.Config") as mock_cfg_cls:
            mock_cfg_cls.load.return_value = fake_cfg
            mcp = APCoreMCP(Registry(), name="test")
        assert "petstore.list_pets" in mcp.tools
