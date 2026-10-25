"""Scoped process ownership of stdout while it carries the MCP protocol."""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sys
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TextIO, cast


@dataclass
class _Owner:
    thread_id: int
    protocol: TextIO
    transport_active: bool = False


_owner: _Owner | None = None
_owner_context: ContextVar[_Owner | None] = ContextVar("apcore_mcp_stdio_owner", default=None)
_owner_lock = threading.Lock()
_selected_transport: ContextVar[str | None] = ContextVar("apcore_mcp_selected_transport", default=None)


def configured_transport(transport: str) -> str:
    """Match the Config Bus transport precedence before constructing a backend."""
    try:
        config_class = importlib.import_module("apcore").Config
    except ImportError:
        return transport
    config = config_class.load()
    configured = config.get("mcp.transport") if config else None
    return configured if isinstance(configured, str) and configured else transport


@contextlib.contextmanager
def _redirect_diagnostics() -> Iterator[TextIO]:
    original_stdout = sys.stdout
    try:
        stdout_fd = original_stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        stdout_fd = stderr_fd = None

    if stdout_fd == 1 and stderr_fd is not None and stderr_fd != 1:
        # Duplicate the protocol endpoint before replacing fd 1. Handlers and
        # native writers that retained stdout then follow the diagnostic fd.
        original_stdout.flush()
        sys.stderr.flush()
        with contextlib.ExitStack() as cleanup:
            protocol_fd = os.dup(1)
            cleanup.callback(os.close, protocol_fd)
            # closefd=False: the stack owns the descriptor, this wrapper alone.
            protocol = cleanup.enter_context(os.fdopen(protocol_fd, "w", encoding="utf-8", closefd=False))
            os.dup2(stderr_fd, 1)
            try:
                yield protocol
            finally:
                try:
                    original_stdout.flush()
                    protocol.flush()
                finally:
                    os.dup2(protocol_fd, 1)
        return

    # Embedders/tests can install non-fd streams. Preserve that stream for MCP
    # and redirect Python lookups plus existing stdout StreamHandlers only.
    handlers = {
        handler
        for logger in [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
        if isinstance(logger, logging.Logger)
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler) and handler.stream is original_stdout
    }
    with contextlib.ExitStack() as cleanup:
        for handler in handlers:
            handler.setStream(sys.stderr)
            cleanup.callback(handler.setStream, original_stdout)
        with contextlib.redirect_stdout(sys.stderr):
            yield cast(TextIO, original_stdout)


@contextlib.contextmanager
def stdio_output(*, transport: bool = False) -> Iterator[TextIO]:
    """Reserve protocol stdout and route diagnostics to stderr until exit.

    Nested API scopes share one owner. A separate thread/context or a second
    active transport fails before changing process output. This is necessarily
    process-wide: a stdio server must be the sole protocol owner in a process.
    """
    global _owner
    current = _owner_context.get()
    if current is not None and current is _owner and current.thread_id == threading.get_ident():
        if transport and current.transport_active:
            raise RuntimeError("MCP stdio is already owned by an active transport")
        if transport:
            current.transport_active = True
        try:
            yield current.protocol
        finally:
            if transport:
                current.transport_active = False
        return

    if not _owner_lock.acquire(blocking=False):
        raise RuntimeError("MCP stdio is already owned by another execution context")
    try:
        with _redirect_diagnostics() as protocol:
            current = _Owner(threading.get_ident(), protocol, transport)
            _owner = current
            token = _owner_context.set(current)
            try:
                yield protocol
            finally:
                _owner_context.reset(token)
                _owner = None
    finally:
        _owner_lock.release()


def output_scope(transport: str) -> contextlib.AbstractContextManager[object]:
    """Only stdio reserves stdout; HTTP/SSE retain the host output policy."""
    return stdio_output() if transport.lower() == "stdio" else contextlib.nullcontext()


@contextlib.contextmanager
def configured_output(transport: str) -> Iterator[None]:
    """Protect configuration loading, then release stdout for HTTP transports.

    Nested delegators reuse the selection rather than loading configuration
    again solely to choose an output scope. Existing serve-time overrides
    retain their normal precedence over the explicit transport argument.
    """
    selected = _selected_transport.get()
    if selected is not None:
        with output_scope(selected):
            yield
        return
    with contextlib.ExitStack() as scope:
        scope.enter_context(stdio_output())
        selected = configured_transport(transport)
        if selected.lower() != "stdio":
            scope.close()
        token = _selected_transport.set(selected)
        try:
            yield
        finally:
            _selected_transport.reset(token)
