"""TransportManager: stdio / Streamable HTTP / SSE transport lifecycle."""

from __future__ import annotations

import contextlib
import logging
import time as _time
import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

import anyio
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

logger = logging.getLogger(__name__)


# Protocol selection belongs to the active transport, never client metadata.
_protocol_source_var: ContextVar[str | None] = ContextVar("apcore_mcp_protocol_source", default=None)

# [TM-4] Module-level ContextVar carrying the active transport session id.
# The transport sets it inside :meth:`TransportManager._scoped_session` so
# that downstream call sites (notably ``factory.handle_call_tool``) can
# tag async-task submissions with ``session_key=transport_session_var.get()``.
# When the transport closes, the bridge's ``cancel_session_tasks`` is
# invoked with that same id to mass-cancel session-bound tasks.
# Mirrors TS ``transportSessionStorage`` (AsyncLocalStorage) and Rust's
# ``TransportManager::set_cancel_handler`` keying scheme.
transport_session_var: ContextVar[str | None] = ContextVar("apcore_mcp_transport_session", default=None)


class _AsyncTaskBridgeProtocol(Protocol):
    """Minimal duck-typed protocol for the bridge object accepted by
    :meth:`TransportManager.set_async_task_bridge` — kept narrow to avoid
    importing :class:`AsyncTaskBridge` (which would create a circular
    dependency between transport and async_task_bridge)."""

    async def cancel_session_tasks(self, session_key: str) -> int: ...


@runtime_checkable
class MetricsExporter(Protocol):
    """Protocol for metrics collectors that can export Prometheus text format."""

    def export_prometheus(self) -> str: ...


class TransportManager:
    """Manages MCP server transport lifecycle."""

    def __init__(self, metrics_collector: MetricsExporter | None = None) -> None:
        self._start_time = _time.monotonic()
        self._metrics_collector: MetricsExporter | None = metrics_collector
        self._module_count: int = 0
        # [TM-4] Optional AsyncTaskBridge — wired in by the server bootstrap
        # so that transport-close events can mass-cancel session-bound tasks.
        self._async_task_bridge: _AsyncTaskBridgeProtocol | None = None

    def set_module_count(self, count: int) -> None:
        """Set the number of registered modules for health reporting."""
        self._module_count = count

    def set_async_task_bridge(self, bridge: _AsyncTaskBridgeProtocol) -> None:
        """Wire an AsyncTaskBridge in for session-bound cancellation.

        [TM-4] Once set, every transport ``_scoped_session`` block invokes
        ``bridge.cancel_session_tasks(session_id)`` on exit so any async
        tasks submitted under that session are cooperatively cancelled
        when the client disconnects. Mirrors TypeScript's
        ``setAsyncTaskBridge`` and Rust's ``set_cancel_handler``.

        Per the feature-spec ``Contract`` block, ``None`` is rejected with
        ``TypeError`` to fail loudly at wire-up rather than silently
        dropping disconnect cancellation.
        """
        if bridge is None:  # type: ignore[unreachable]
            raise TypeError("set_async_task_bridge() requires a non-None bridge")
        self._async_task_bridge = bridge

    @contextlib.asynccontextmanager
    async def _scoped_session(self, session_id: str) -> AsyncIterator[None]:
        """Context manager bracketing a transport session.

        [TM-4] On enter: publishes ``session_id`` via
        :data:`transport_session_var` so nested call sites (factory's
        ``handle_call_tool``) can tag async-task submissions with the
        owning session.

        On exit: resets the contextvar and, when an
        :class:`AsyncTaskBridge` has been configured via
        :meth:`set_async_task_bridge`, calls
        ``bridge.cancel_session_tasks(session_id)`` to mass-cancel any
        session-bound tasks. Errors raised by the bridge are logged and
        swallowed — disconnect cleanup must never bubble out and crash
        the transport. Mirrors TS+Rust behaviour.
        """
        token = transport_session_var.set(session_id)
        try:
            yield
        finally:
            transport_session_var.reset(token)
            bridge = self._async_task_bridge
            if bridge is not None:
                try:
                    await bridge.cancel_session_tasks(session_id)
                except Exception:  # noqa: BLE001 — disconnect must not propagate
                    logger.warning(
                        "cancel_session_tasks(%s) failed during transport teardown",
                        session_id,
                        exc_info=True,
                    )

    async def _run_scoped(
        self,
        server: Server,
        read_stream: Any,
        write_stream: Any,
        init_options: InitializationOptions,
        session_id: str,
        streamable_http: bool = False,
    ) -> None:
        """Run ``server.run`` bracketed by :meth:`_scoped_session`.

        [TM-4] Single choke point so every transport gets identical
        session-scoping behaviour: the session id is published via
        ``transport_session_var`` for the protocol session's lifetime, and
        ``AsyncTaskBridge.cancel_session_tasks`` fires on teardown.
        """
        async with self._scoped_session(session_id):
            token = _protocol_source_var.set("http" if streamable_http else "session")
            try:
                await server.run(read_stream, write_stream, init_options)
            finally:
                _protocol_source_var.reset(token)

    def _build_health_response(self) -> dict[str, object]:
        """Build health check response."""
        return {
            "status": "ok",
            "uptime_seconds": round(_time.monotonic() - self._start_time, 1),
            "module_count": self._module_count,
        }

    def _build_metrics_response(self) -> Response:
        """Build Prometheus metrics response.

        Returns 200 with Prometheus text if a metrics collector is configured,
        or 404 if no collector is available.
        """
        if self._metrics_collector is None:
            return Response(status_code=404)
        body = self._metrics_collector.export_prometheus()
        return Response(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @contextlib.asynccontextmanager
    async def build_streamable_http_app(
        self,
        server: Server,
        init_options: InitializationOptions,
        *,
        extra_routes: list[Route | Mount] | None = None,
        middleware: list[tuple[type, dict[str, Any]]] | None = None,
    ) -> AsyncIterator[Starlette]:
        """Build a Starlette ASGI app for Streamable HTTP transport.

        Returns a Starlette app that can be mounted into a larger application.
        Must be used as an async context manager — the MCP protocol session
        runs as a background task for the lifetime of the context.

        Example::

            async with transport_manager.build_streamable_http_app(
                server, init_options
            ) as mcp_app:
                # Mount mcp_app into a parent Starlette app, then run uvicorn.
                combined = Starlette(routes=[
                    Mount("/mcp", app=mcp_app),
                    Mount("/a2a", app=a2a_app),
                ])
                await uvicorn.Server(
                    uvicorn.Config(combined, host="0.0.0.0", port=8000)
                ).serve()
        """
        session_id = uuid.uuid4().hex
        transport = StreamableHTTPServerTransport(
            mcp_session_id=session_id,
        )

        async with transport.connect() as (read_stream, write_stream):

            async def _health(request: Any) -> JSONResponse:
                return JSONResponse(self._build_health_response())

            async def _metrics(request: Any) -> Response:
                return self._build_metrics_response()

            routes: list[Route | Mount] = [
                Route("/health", endpoint=_health, methods=["GET"]),
                Route("/metrics", endpoint=_metrics, methods=["GET"]),
            ]
            if extra_routes:
                routes.extend(extra_routes)
            routes.append(Mount("/mcp", app=transport.handle_request))

            app: Any = Starlette(routes=routes)
            if middleware:
                for mw_cls, mw_kwargs in middleware:
                    app = mw_cls(app, **mw_kwargs)

            # [TM-4] Bracket the protocol session so ``transport_session_var``
            # is populated for async-task submissions and so the configured
            # AsyncTaskBridge mass-cancels session-bound tasks on teardown.
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._run_scoped, server, read_stream, write_stream, init_options, session_id, True)
                yield app
                tg.cancel_scope.cancel()

    async def run_stdio(
        self,
        server: Server,
        init_options: InitializationOptions,
    ) -> None:
        """Start MCP server with stdio transport. Blocks until connection closes."""
        logger.info("Starting stdio transport")
        # [TM-4] stdio has no wire-level session id; synthesize one so async
        # tasks submitted over this connection are still tracked and cancelled
        # when the stdio stream closes.
        session_id = uuid.uuid4().hex
        async with stdio_server() as (read_stream, write_stream):
            await self._run_scoped(server, read_stream, write_stream, init_options, session_id)

    async def run_streamable_http(
        self,
        server: Server,
        init_options: InitializationOptions,
        host: str = "127.0.0.1",
        port: int = 8000,
        extra_routes: list[Route | Mount] | None = None,
        middleware: list[tuple[type, dict[str, Any]]] | None = None,
    ) -> None:
        """Start MCP server with Streamable HTTP transport."""
        self._validate_host_port(host, port)
        logger.info("Starting streamable-http transport on %s:%d", host, port)

        session_id = uuid.uuid4().hex
        transport = StreamableHTTPServerTransport(
            mcp_session_id=session_id,
        )

        async with transport.connect() as (read_stream, write_stream):

            async def _health(request: Any) -> JSONResponse:
                return JSONResponse(self._build_health_response())

            async def _metrics(request: Any) -> Response:
                return self._build_metrics_response()

            routes: list[Route | Mount] = [
                Route("/health", endpoint=_health, methods=["GET"]),
                Route("/metrics", endpoint=_metrics, methods=["GET"]),
            ]
            if extra_routes:
                routes.extend(extra_routes)
            routes.append(Mount("/mcp", app=transport.handle_request))

            app: Any = Starlette(routes=routes)
            if middleware:
                for mw_cls, mw_kwargs in middleware:
                    app = mw_cls(app, **mw_kwargs)

            config = uvicorn.Config(app, host=host, port=port, log_level="info")
            uv_server = uvicorn.Server(config)

            # Run both the MCP server and HTTP server concurrently. [TM-4] The
            # MCP protocol session is bracketed by ``_scoped_session``.
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._run_scoped, server, read_stream, write_stream, init_options, session_id, True)
                tg.start_soon(uv_server.serve)

    async def run_sse(
        self,
        server: Server,
        init_options: InitializationOptions,
        host: str = "127.0.0.1",
        port: int = 8000,
        extra_routes: list[Route | Mount] | None = None,
        middleware: list[tuple[type, dict[str, Any]]] | None = None,
    ) -> None:
        """Start MCP server with SSE transport (deprecated)."""
        self._validate_host_port(host, port)
        logger.info("Starting sse transport on %s:%d", host, port)
        logger.warning("SSE transport is deprecated. Use Streamable HTTP instead.")

        sse_transport = SseServerTransport("/messages/")

        async def handle_sse(request: Any) -> Response:
            # [TM-4] Each SSE connection is an independent protocol session;
            # synthesize a per-connection id so its async tasks are tracked
            # and cancelled when the SSE stream closes.
            session_id = uuid.uuid4().hex
            async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (
                read_stream,
                write_stream,
            ):
                await self._run_scoped(server, read_stream, write_stream, init_options, session_id)
            return Response()

        async def _health(request: Any) -> JSONResponse:
            return JSONResponse(self._build_health_response())

        async def _metrics(request: Any) -> Response:
            return self._build_metrics_response()

        routes: list[Route | Mount] = [
            Route("/health", endpoint=_health, methods=["GET"]),
            Route("/metrics", endpoint=_metrics, methods=["GET"]),
        ]
        if extra_routes:
            routes.extend(extra_routes)
        routes.extend(
            [
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse_transport.handle_post_message),
            ]
        )

        app: Any = Starlette(routes=routes)
        if middleware:
            for mw_cls, mw_kwargs in middleware:
                app = mw_cls(app, **mw_kwargs)

        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        uv_server = uvicorn.Server(config)
        await uv_server.serve()

    def _validate_host_port(self, host: str, port: int) -> None:
        """Validate host and port parameters."""
        if not host:
            raise ValueError("Host must not be empty")
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {port}")
