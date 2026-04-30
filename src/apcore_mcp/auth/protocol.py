"""Authenticator protocol for pluggable authentication backends."""

from __future__ import annotations

import inspect
from typing import Awaitable, Protocol, Union, runtime_checkable

from apcore import Identity


@runtime_checkable
class Authenticator(Protocol):
    """Protocol for authentication backends.

    Implementations extract credentials from HTTP headers and return
    an ``Identity`` on success, or ``None`` on failure.

    [JWT-1] ``authenticate`` may be either a regular function or a coroutine
    function. The first-party :class:`apcore_mcp.auth.jwt.JWTAuthenticator`
    is async to align with the TypeScript and Rust implementations and to
    leave room for I/O-bound backends (JWKS rotation, OAuth introspection).
    Sync custom authenticators continue to work — call sites bridge via
    :func:`call_authenticator`, which awaits awaitables and passes through
    plain return values unchanged.
    """

    def authenticate(  # noqa: D401 — protocol shape, may be sync or async
        self, headers: dict[str, str]
    ) -> Union[Identity, None, Awaitable[Identity | None]]:
        """Authenticate a request from its headers.

        Args:
            headers: Lowercase header keys mapped to their values.

        Returns:
            An ``Identity`` if authentication succeeds, ``None`` otherwise.
            Implementations may return either the value directly or an
            awaitable resolving to it.
        """
        ...


async def call_authenticator(
    authenticator: Authenticator, headers: dict[str, str]
) -> Identity | None:
    """Invoke ``authenticator.authenticate`` and bridge sync/async returns.

    [JWT-1] Since :class:`Authenticator` permits either sync or async
    implementations, callers in async contexts use this helper to invoke
    the authenticator without caring which form was supplied. The helper
    inspects the return value and awaits it only when it is awaitable —
    so a sync custom authenticator returning ``Identity`` directly works
    transparently alongside the async :class:`JWTAuthenticator`.
    """
    result = authenticator.authenticate(headers)
    if inspect.isawaitable(result):
        return await result  # type: ignore[no-any-return]
    return result  # type: ignore[return-value]
