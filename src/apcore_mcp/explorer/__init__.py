"""MCP Tool Explorer: browser-based UI for inspecting and testing MCP tools.

Thin adapter over ``mcp-embedded-ui`` — delegates all route handling and
HTML rendering to the external library while bridging apcore authentication.
"""

from __future__ import annotations

import contextlib
from typing import Any

from mcp_embedded_ui import create_mount
from starlette.requests import Request
from starlette.routing import Mount

from apcore_mcp.auth.middleware import auth_identity_var, extract_headers


def _build_auth_hook(authenticator: Any):  # noqa: ANN202
    """Build an ``auth_hook`` context manager from an apcore Authenticator.

    The hook extracts the Bearer token from the request, authenticates via
    the provided authenticator, and sets ``auth_identity_var`` for the
    duration of the tool call.  If authentication fails the hook raises,
    which causes mcp-embedded-ui to return 401.
    """

    @contextlib.contextmanager
    def auth_hook(request: Request):  # noqa: ANN202
        headers = extract_headers(request.scope)
        identity = authenticator.authenticate(headers)
        if identity is None:
            raise ValueError("Missing or invalid Bearer token")
        token = auth_identity_var.set(identity)
        try:
            yield
        finally:
            auth_identity_var.reset(token)

    return auth_hook


def create_explorer_mount(
    tools: list[Any],
    router: Any,
    *,
    allow_execute: bool = False,
    explorer_prefix: str = "/explorer",
    authenticator: Any | None = None,
    title: str = "MCP Tool Explorer",
    project_name: str | None = None,
    project_url: str | None = None,
) -> Mount:
    """Create a Starlette Mount for the MCP Tool Explorer.

    Args:
        tools: List of MCP Tool objects to expose in the explorer.
        router: An ExecutionRouter for executing tool calls.
        allow_execute: Whether to allow tool execution from the explorer UI.
        explorer_prefix: URL prefix for the explorer (default: "/explorer").
        authenticator: Optional Authenticator for per-request identity in tool execution.
        title: Page title shown in the browser tab and heading.
        project_name: Optional project name shown in the explorer footer.
        project_url: Optional project URL linked in the explorer footer.

    Returns:
        A Starlette Mount that can be included in the app's route list.
    """
    auth_hook = _build_auth_hook(authenticator) if authenticator is not None else None

    async def _handle_call(
        name: str,
        args: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        return await router.handle_call(name, args)

    return create_mount(
        explorer_prefix,
        tools=tools,
        handle_call=_handle_call,
        allow_execute=allow_execute,
        auth_hook=auth_hook,
        title=title,
        project_name=project_name,
        project_url=project_url,
    )
