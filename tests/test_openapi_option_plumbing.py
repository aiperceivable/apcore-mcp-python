"""Option-plumbing regressions for the OpenAPI backend.

Distinct from ``test_openapi_config_bus_wiring.py``, which covers whether
``openapi_backend`` is reached from the Config Bus at all. This file covers
whether the OPTIONS survive that trip.

Python is the reference implementation for three defects found in the other
two bridges — ``headers`` never reaching the fetch (apcore-mcp-rust#8),
``timeout`` configuring the proxy instead of the fetch (apcore-mcp-rust#9,
and as milliseconds in apcore-mcp-typescript#10), and ``Config.project_root``
never being read (apcore-mcp#19). It already behaves correctly; nothing here
is a bug fix. These tests exist so it cannot drift into the same shape, since
the conformance suite hands the backend an already-parsed document and calls
``resolve_spec_location`` directly with an explicit ``project_root`` — it
covers the pure functions and never the wiring between them.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from apcore_mcp.openapi_backend import build_openapi_backend_from_config, openapi_backend

_PETSTORE: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/pets": {
            "get": {"operationId": "listPets", "responses": {"200": {"description": "ok"}}},
        }
    },
}

# Long enough that a millisecond-scale budget cannot clear it.
_SERVE_DELAY_SECONDS = 0.12


class _SpecServer:
    """Serve the spec after a delay, recording the headers it was sent."""

    def __init__(self) -> None:
        self.seen_headers: dict[str, str] = {}
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                server_self.seen_headers = {k.lower(): v for k, v in self.headers.items()}
                import time

                time.sleep(_SERVE_DELAY_SECONDS)
                body = json.dumps(_PETSTORE).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                """Silence the default stderr access log."""

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_port}/openapi.json"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def spec_server():
    server = _SpecServer()
    yield server
    server.close()


def _ids(registry: Any) -> list[str]:
    return registry.list(visibility=["public", "hidden"])


# ---------------------------------------------------------------------------
# headers reach the spec fetch (apcore-mcp-rust#8)
# ---------------------------------------------------------------------------


def test_config_headers_reach_the_spec_fetch(spec_server: _SpecServer) -> None:
    registry = build_openapi_backend_from_config(
        {
            "spec": spec_server.url,
            "base_url": "https://api.example.com",
            "headers": {"X-Api-Key": "s3cret", "X-Tenant": "acme"},
        }
    )
    assert "listpets" in _ids(registry)
    assert spec_server.seen_headers.get("x-api-key") == "s3cret"
    assert spec_server.seen_headers.get("x-tenant") == "acme"


def test_option_headers_reach_the_spec_fetch(spec_server: _SpecServer) -> None:
    openapi_backend(
        spec_server.url,
        base_url="https://api.example.com",
        headers={"X-Api-Key": "from-cli"},
    )
    assert spec_server.seen_headers.get("x-api-key") == "from-cli"


# ---------------------------------------------------------------------------
# timeout is the spec-fetch budget, in seconds (apcore-mcp-rust#9, -typescript#10)
# ---------------------------------------------------------------------------


def test_documented_default_timeout_fetches_a_slow_spec(spec_server: _SpecServer) -> None:
    # The documented default is 30 *seconds*. Read as milliseconds it would be
    # 30 ms, far under this server's 120 ms — the TypeScript defect.
    registry = build_openapi_backend_from_config({"spec": spec_server.url, "base_url": "https://api.example.com"})
    assert "listpets" in _ids(registry)


def test_a_short_timeout_aborts_the_spec_fetch(spec_server: _SpecServer) -> None:
    # 0.01 s < the server's 120 ms. Were `timeout` routed to the proxy writer
    # instead — the Rust defect — the fetch would run on the default and this
    # would succeed.
    import httpx

    with pytest.raises(httpx.TimeoutException):
        build_openapi_backend_from_config(
            {
                "spec": spec_server.url,
                "base_url": "https://api.example.com",
                "timeout": 0.01,
            }
        )


def test_a_short_timeout_does_not_shrink_the_proxy_timeout() -> None:
    # `timeout` is spec-fetch only, so a tiny value must leave proxied calls on
    # apcore-toolkit's own 60 s default. An already-parsed document performs no
    # fetch, so the only thing the value could reach here is the proxy writer —
    # which rejects a non-positive timeout and would otherwise bind 0.01 s to
    # every proxied call.
    registry = build_openapi_backend_from_config(
        {"spec": _PETSTORE, "base_url": "https://api.example.com", "timeout": 0.01}
    )
    assert "listpets" in _ids(registry)


# ---------------------------------------------------------------------------
# a relative spec resolves against Config.project_root (apcore-mcp#19)
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, project_root: str) -> None:
        self.project_root = project_root


def test_relative_spec_resolves_against_config_project_root(tmp_path: Path) -> None:
    (tmp_path / "openapi.json").write_text(json.dumps(_PETSTORE))
    assert tmp_path != Path.cwd()

    with patch("apcore.config.Config") as config_cls:
        config_cls.get_instance.return_value = _FakeConfig(str(tmp_path))
        registry = build_openapi_backend_from_config({"spec": "./openapi.json", "base_url": "https://api.example.com"})
    assert "listpets" in _ids(registry)


def test_explicit_project_root_wins_over_config(tmp_path: Path) -> None:
    (tmp_path / "openapi.json").write_text(json.dumps(_PETSTORE))

    with patch("apcore.config.Config") as config_cls:
        config_cls.get_instance.return_value = _FakeConfig("/nonexistent-project-root")
        registry = openapi_backend(
            "./openapi.json",
            base_url="https://api.example.com",
            project_root=str(tmp_path),
        )
    assert "listpets" in _ids(registry)


def test_falls_back_to_cwd_when_config_has_no_project_root() -> None:
    with patch("apcore.config.Config") as config_cls:
        config_cls.get_instance.return_value = _FakeConfig("")
        with pytest.raises(FileNotFoundError):
            openapi_backend("./definitely-not-here.json", base_url="https://api.example.com")
