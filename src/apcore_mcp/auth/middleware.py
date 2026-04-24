"""ASGI middleware that bridges HTTP authentication to MCP via ContextVar."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from apcore import Identity

from apcore_mcp.auth.protocol import Authenticator

logger = logging.getLogger(__name__)

# Bridge between ASGI middleware and MCP handler
auth_identity_var: ContextVar[Identity | None] = ContextVar("auth_identity", default=None)


def extract_headers(scope: dict[str, Any]) -> dict[str, str]:
    """Extract headers from ASGI scope as a lowercase-key dict."""
    result: dict[str, str] = {}
    for key_bytes, value_bytes in scope.get("headers", []):
        result[key_bytes.decode("latin-1").lower()] = value_bytes.decode("latin-1")
    return result


class AuthMiddleware:
    """ASGI middleware that authenticates requests and sets ``auth_identity_var``.

    Args:
        app: The ASGI application to wrap.
        authenticator: An ``Authenticator`` implementation.
        exempt_paths: Exact paths that bypass authentication.
        exempt_prefixes: Path prefixes that bypass authentication.
            Any request whose path starts with one of these prefixes is exempt.
        require_auth: If True, unauthenticated requests receive 401.
            If False, requests proceed without identity (permissive mode).
    """

    def __init__(
        self,
        app: Any,
        authenticator: Authenticator,
        *,
        exempt_paths: set[str] | None = None,
        exempt_prefixes: set[str] | None = None,
        require_auth: bool = True,
    ) -> None:
        self._app = app
        self._authenticator = authenticator
        self._exempt_paths = exempt_paths if exempt_paths is not None else {"/health", "/metrics"}
        self._exempt_prefixes = exempt_prefixes or set()
        self._require_auth = require_auth

    def _is_exempt(self, path: str) -> bool:
        """Check if a path is exempt from authentication."""
        if path in self._exempt_paths:
            return True
        return any(path.startswith(prefix) for prefix in self._exempt_prefixes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_exempt(path):
            # Best-effort identity extraction: exempt paths don't *require* auth,
            # but if a valid token is present we still populate identity so that
            # downstream handlers (e.g. require_user_id) can use it.
            identity = None
            try:
                headers = extract_headers(scope)
                identity = self._authenticator.authenticate(headers)
            except Exception:
                logger.warning(
                    "Authenticator raised on exempt path %s — proceeding with identity=None",
                    path,
                    exc_info=True,
                )
            token = auth_identity_var.set(identity)
            try:
                await self._app(scope, receive, send)
            finally:
                auth_identity_var.reset(token)
            return

        headers = extract_headers(scope)
        identity = self._authenticator.authenticate(headers)

        if identity is None and self._require_auth:
            logger.warning("Authentication failed for %s", path)
            await self._send_401(send)
            return

        token = auth_identity_var.set(identity)
        try:
            await self._app(scope, receive, send)
        finally:
            auth_identity_var.reset(token)

    @staticmethod
    async def _send_401(send: Any) -> None:
        """Send a 401 Unauthorized JSON response."""
        body = json.dumps({"error": "Unauthorized", "detail": "Missing or invalid Bearer token"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"www-authenticate", b"Bearer"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
